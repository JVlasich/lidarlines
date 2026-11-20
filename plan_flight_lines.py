"""
UAV flight-line planner - QGIS Processing script.

Draw an AOI polygon, generate flight lines, export to KML, load Waypoints onto the map.
Qgis wrapper over the pip-installed uavplanner package.
"""

import os
import tempfile

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProviderRegistry,
    QgsVectorFileWriter,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

# Try to import the main module
try:
    from uavplanner.planner import plan_mission
    from uavplanner.planner_types import MissionParams
    from uavplanner.reader import read_polygon
    from uavplanner.writer import write_kml
except ImportError as exc:
    raise ImportError(
        "uavplanner is not installed in QGIS's Python,"
        "consult readme for installation options"
    ) from exc


class PlanFlightLines(QgsProcessingAlgorithm):

    AOI = "AOI"
    FEATURE = "FEATURE"
    ALTITUDE = "ALTITUDE"
    VELOCITY = "VELOCITY"
    FOV = "FOV"
    AUTO_AZIMUTH = "AUTO_AZIMUTH"
    AZIMUTH = "AZIMUTH"
    OVERLAP = "OVERLAP"
    LEAD_IN = "LEAD_IN"
    PRF = "PRF"
    CRS = "CRS"
    MISSION = "MISSION"
    RESTRICTED = "RESTRICTED"
    OUTPUT = "OUTPUT"

    MISSION_TYPES = ("single_grid", "double_grid")

    # --- inputs --------------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.AOI, "Area of interest", [QgsProcessing.TypeVectorPolygon]))

        self.addParameter(QgsProcessingParameterNumber(
            self.FEATURE, "Feature number (1-based; used when AOI has several)",
            type=QgsProcessingParameterNumber.Integer, minValue=1, defaultValue=1))

        self.addParameter(QgsProcessingParameterNumber(
            self.ALTITUDE, "Altitude (m)",
            type=QgsProcessingParameterNumber.Double, minValue=0.0, defaultValue=70.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.VELOCITY, "Velocity (m/s)",
            type=QgsProcessingParameterNumber.Double, minValue=0.0, defaultValue=5.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.FOV, "Field of view (deg)",
            type=QgsProcessingParameterNumber.Double, minValue=0.0, maxValue=180.0, defaultValue=60.0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.AUTO_AZIMUTH, "Auto azimuth (shortest path)", defaultValue=True))

        self.addParameter(QgsProcessingParameterNumber(
            self.AZIMUTH, "Flight azimuth (deg, used only when auto is off)",
            type=QgsProcessingParameterNumber.Double, minValue=-359.999, maxValue=359.999, defaultValue=0.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.OVERLAP, "Overlap (fraction 0..1)",
            type=QgsProcessingParameterNumber.Double,
            minValue=0.0, maxValue=0.999, defaultValue=0.05))

        self.addParameter(QgsProcessingParameterNumber(
            self.LEAD_IN, "Lead-in distance (m)",
            type=QgsProcessingParameterNumber.Double, minValue=0.0, defaultValue=0.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.PRF, "PRF (Hz)",
            type=QgsProcessingParameterNumber.Double, minValue=0.0, defaultValue=300000.0))

        self.addParameter(QgsProcessingParameterCrs(
            self.CRS, "Projected CRS for computation (blank = auto UTM)",
            defaultValue=None, optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.MISSION, "Mission type", options=self.MISSION_TYPES, defaultValue=0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.RESTRICTED, "Restrict airspace so flight doesnt leave AOI", defaultValue=False))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Flight lines (KML)", fileFilter="KML files (*.kml)"))

    # --- processing --------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        # 1) read every parameter back out --------------------------------------
        # Optional projected CRS: blank -> auto UTM (epsg=None). Reject geographic.
        crs = self.parameterAsCrs(parameters, self.CRS, context)
        epsg = None
        if crs.isValid():
            if crs.isGeographic():
                raise QgsProcessingException(
                    "Pick a projected CRS (metres) or leave blank for auto UTM; "
                    f"{crs.authid()} is geographic.")
            authid = crs.authid()
            epsg = int(authid.split(":")[1]) if authid.startswith("EPSG:") else crs.postgisSrid()

        auto_az = self.parameterAsBool(parameters, self.AUTO_AZIMUTH, context)
        flight_azimuth = None if auto_az else self.parameterAsDouble(parameters, self.AZIMUTH, context)

        restricted = self.parameterAsBool(parameters, self.RESTRICTED, context)
        lead_in = self.parameterAsDouble(parameters, self.LEAD_IN, context)
        if restricted and lead_in > 0:
            raise QgsProcessingException(
                "Lead-in extends lines beyond the AOI; not allowed with restricted.")

        mission_idx = self.parameterAsEnum(parameters, self.MISSION, context)

        params = MissionParams(
            altitude=self.parameterAsDouble(parameters, self.ALTITUDE, context),
            velocity=self.parameterAsDouble(parameters, self.VELOCITY, context),
            fov=self.parameterAsDouble(parameters, self.FOV, context),
            flight_azimuth=flight_azimuth,
            overlap=self.parameterAsDouble(parameters, self.OVERLAP, context),
            lead_in=lead_in,
            epsg=epsg,
            prf=self.parameterAsDouble(parameters, self.PRF, context),
            mission_type=self.MISSION_TYPES[mission_idx],
            restricted=restricted,
        )

        # translate qgis aoi to geojson
        source = self.parameterAsSource(parameters, self.AOI, context)
        if source is None:
            raise QgsProcessingException("No area of interest provided.")

        count = source.featureCount()
        feat_index = self.parameterAsInt(parameters, self.FEATURE, context)
        if feat_index > count:
            raise QgsProcessingException(
                f"Feature number {feat_index} out of range; AOI has {count} feature(s).")
        if count > 1:
            feedback.pushInfo(f"AOI has {count} features; using feature #{feat_index}.")

        chosen = None
        for i, feat in enumerate(source.getFeatures(), start=1):
            if i == feat_index:
                chosen = feat
                break
        if chosen is None or not chosen.hasGeometry():
            raise QgsProcessingException("AOI feature has no geometry.")
        geom = chosen.geometry()
        if geom.isMultipart() and sum(1 for _ in geom.parts()) > 1:
            raise QgsProcessingException("AOI must be a single polygon (got a multipart geometry).")

        tmp_dir = tempfile.mkdtemp(prefix="uavplanner_")
        aoi_path = os.path.join(tmp_dir, "aoi.geojson")

        save_opts = QgsVectorFileWriter.SaveVectorOptions()
        save_opts.driverName = "GeoJSON"
        # read_polygon expects lon/lat (WGS84); always reproject the AOI there.
        save_opts.ct = QgsCoordinateTransform(
            source.sourceCrs(), QgsCoordinateReferenceSystem("EPSG:4326"),
            context.transformContext())
        err, msg, _, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
            source.materialize(QgsFeatureRequest().setFilterFid(chosen.id())),
            aoi_path, context.transformContext(), save_opts)
        if err != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(f"Could not write AOI GeoJSON: {msg}")
        feedback.pushInfo(f"AOI written to {aoi_path}")

        if feedback.isCanceled():
            return {}

        # --- run -----------------------------------------------------
        polygon = read_polygon(aoi_path)
        plan = plan_mission(polygon, params)
        feedback.pushInfo(f"Planned mission — metrics: {plan.metrics}")

        # --- write KML -----------------------------------------------
        kml_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        write_kml(plan, kml_path)

        # --- load to map ---------------------------------------------
        # Resolve geometry types so both come in as separate layers.
        flags = Qgis.SublayerQueryFlag.ResolveGeometryType
        sublayers = QgsProviderRegistry.instance().querySublayers(kml_path, flags)
        if not sublayers:
            raise QgsProcessingException(f"No layers found in {kml_path}")

        for sub in sublayers:
            geom = QgsWkbTypes.geometryType(sub.wkbType())
            if geom == QgsWkbTypes.PointGeometry:
                layer_name = "Waypoints"
            elif geom == QgsWkbTypes.LineGeometry:
                layer_name = "Flight path"
            else:
                layer_name = sub.name()
            details = QgsProcessingContext.LayerDetails(
                layer_name, context.project(), self.OUTPUT)
            context.addLayerToLoadOnCompletion(sub.uri(), details)

        return {self.OUTPUT: kml_path}

    # Force the algorithm onto the main thread. fixes pyproj bug that crashed qgis
    def flags(self):
        return super().flags() | Qgis.ProcessingAlgorithmFlag.NoThreading

    # --- identity --------------------------------------------------------------
    def name(self):
        return "planflightlines"

    def displayName(self):
        return "Plan flight lines"

    def group(self):
        return "UAV planning"

    def groupId(self):
        return "uavplanning"

    def shortHelpString(self):
        return "Generates drone flight lines for a drawn AOI and exports them to KML."

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        return PlanFlightLines()
