#!/usr/bin/env python3
"""
Land Use Map Maker + Watershed Delineation
Version: 0.5.0
Interactive Streamlit app combining:
- OpenStreetMap landuse data
- Custom shapefiles
- SRTM DEM + Watershed delineation (pysheds)
- Multiple classified raster support + zonal statistics
- ArcGIS REST API layer support
"""

# App Version
APP_VERSION = "0.5.0"

import streamlit as st
import folium
from streamlit_folium import st_folium
import osmnx as ox
import geopandas as gpd
from shapely.geometry import box, Polygon, shape
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib import cm as mpl_cm
import branca.colormap as cm
import zipfile
import tempfile
import os
import io
import requests
from typing import Optional, Dict, List, Tuple, Any
import warnings
warnings.filterwarnings('ignore')


def gdf_to_shapefile_zip(gdf: gpd.GeoDataFrame, layer_name: str = "layer") -> bytes:
    """Convert a GeoDataFrame to a zipped Shapefile (in memory)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        shp_path = os.path.join(tmpdir, f"{layer_name}.shp")
        gdf.to_file(shp_path, driver="ESRI Shapefile")
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                file_path = os.path.join(tmpdir, f"{layer_name}{ext}")
                if os.path.exists(file_path):
                    zipf.write(file_path, arcname=f"{layer_name}{ext}")
        zip_buffer.seek(0)
        return zip_buffer.getvalue()

# Raster / Hydrology
try:
    import rasterio
    from rasterio.features import shapes as rasterio_shapes
    from affine import Affine
    from rasterio.mask import mask as rio_mask
    HAS_RASTER = True
except ImportError:
    HAS_RASTER = False

try:
    from pysheds.grid import Grid
    HAS_PYSHEDS = True
except ImportError:
    HAS_PYSHEDS = False

try:
    import elevation
    HAS_ELEVATION = True
except ImportError:
    HAS_ELEVATION = False

# Page configuration
st.set_page_config(
    page_title="Land Use Map Maker + Watershed Tools",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {font-size: 2.4rem; font-weight: 700; color: #1f77b4; margin-bottom: 0.3rem;}
    .sub-header {font-size: 1.05rem; color: #555; margin-bottom: 1.2rem;}
    .stButton>button {width: 100%;}
    .success-box {background-color: #d4edda; padding: 0.8rem; border-radius: 0.4rem; border-left: 5px solid #28a745;}
    .warning-box {background-color: #fff3cd; padding: 0.8rem; border-radius: 0.4rem;}
</style>
""", unsafe_allow_html=True)

# ==================== COLOR DICTS ====================
OSM_LANDUSE_COLORS: Dict[str, str] = {
    'residential': '#e41a1c', 'commercial': '#ff7f00', 'industrial': '#377eb8',
    'retail': '#f781bf', 'farmland': '#4daf4a', 'forest': '#006400',
    'meadow': '#7fc97f', 'grass': '#a6d854', 'water': '#1f78b4',
    'wetland': '#80b1d3', 'cemetery': '#bebada', 'military': '#d9d9d9',
    'quarry': '#8c510a', 'landfill': '#d94801', 'construction': '#fdbf6f',
    'allotments': '#ffff99', 'vineyard': '#b3de69', 'orchard': '#ccebc5',
    'default': '#9e9e9e'
}

def get_landuse_color(landuse_value: str) -> str:
    if pd.isna(landuse_value):
        return OSM_LANDUSE_COLORS['default']
    key = str(landuse_value).lower().strip()
    return OSM_LANDUSE_COLORS.get(key, OSM_LANDUSE_COLORS['default'])

def generate_categorical_colors(values: List[str]) -> Dict[str, str]:
    n = len(values)
    if n == 0:
        return {}
    cmap = mpl_cm.get_cmap('tab20', n)
    colors = {}
    for i, val in enumerate(values):
        rgba = cmap(i)
        colors[str(val)] = mcolors.to_hex(rgba)
    return colors

def calculate_area_stats(gdf: gpd.GeoDataFrame, class_col: str, name: str = "Layer") -> pd.DataFrame:
    if gdf is None or gdf.empty or class_col not in gdf.columns:
        return pd.DataFrame()
    gdf = gdf.copy()
    try:
        gdf_area = gdf.to_crs(epsg=54009)
    except Exception:
        gdf_area = gdf.to_crs(epsg=3857)
    gdf_area['area_ha'] = gdf_area.geometry.area / 10000
    gdf_area['area_km2'] = gdf_area['area_ha'] / 100
    stats = gdf_area.groupby(class_col).agg({
        'area_ha': 'sum', 'area_km2': 'sum', 'geometry': 'count'
    }).rename(columns={'geometry': 'polygon_count'}).reset_index()
    stats = stats.sort_values('area_ha', ascending=False)
    stats['layer'] = name
    return stats[['layer', class_col, 'polygon_count', 'area_ha', 'area_km2']]

# ==================== WATERSHED FUNCTIONS ====================

def download_dem_bmi(aoi_gdf: gpd.GeoDataFrame, dem_type: str = "SRTMGL3", buffer_deg: float = 0.05) -> Optional[str]:
    """
    Download SRTM DEM using bmi-topography (recommended).
    Uses OpenTopography REST API - more reliable than the old 'elevation' package.
    """
    try:
        from bmi_topography import Topography
    except ImportError:
        st.error("`bmi-topography` package is not installed. Please run: pip install bmi-topography")
        return None

    if aoi_gdf is None or aoi_gdf.empty:
        st.error("No AOI defined.")
        return None

    try:
        minx, miny, maxx, maxy = aoi_gdf.total_bounds
        minx -= buffer_deg
        miny -= buffer_deg
        maxx += buffer_deg
        maxy += buffer_deg

        st.info(f"Downloading {dem_type} from OpenTopography...")

        topo = Topography(
            dem_type=dem_type,
            south=miny,
            north=maxy,
            west=minx,
            east=maxx,
            output_format="GTiff",
            cache_dir="."
        )

        dem_path = topo.fetch()

        if dem_path and os.path.exists(dem_path):
            # Use cross-platform temp directory instead of hardcoding /tmp
            temp_dir = tempfile.gettempdir()
            persistent_path = os.path.join(temp_dir, f"dem_{dem_type}_{os.getpid()}.tif")
            import shutil
            shutil.copy(dem_path, persistent_path)
            return persistent_path
        else:
            st.error("Download completed but file not found.")
            return None

    except Exception as e:
        st.error(f"DEM download failed: {str(e)}")
        st.warning(
            "Auto-download using OpenTopography can sometimes fail.\n\n"
            "**Recommended:** Use the **Upload your own DEM** option below for reliability (especially on Windows)."
        )
        return None

def vectorize_watershed(grid: Any, catchment: Any, crs: str = "EPSG:4326") -> Optional[gpd.GeoDataFrame]:
    """Convert pysheds catchment raster to a GeoDataFrame polygon."""
    try:
        affine = grid.affine
        results = list(rasterio_shapes(
            catchment.astype(np.uint8), 
            mask=catchment > 0,
            transform=affine
        ))
        if not results:
            return None
        geometries = [shape(geom) for geom, val in results]
        gdf = gpd.GeoDataFrame(geometry=geometries, crs=grid.crs or crs)
        if len(gdf) > 1:
            gdf = gdf.dissolve()
        return gdf.reset_index(drop=True)
    except Exception as e:
        st.error(f"Failed to vectorize watershed: {e}")
        return None


def compute_zonal_stats_from_raster(raster_path: str, mask_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Compute area per class inside the mask geometry (e.g. watershed).
    Assumes the raster is classified (integer values = classes).
    """
    if not HAS_RASTER or mask_gdf is None or mask_gdf.empty:
        return pd.DataFrame()

    try:
        with rasterio.open(raster_path) as src:
            # Reproject mask to raster CRS if needed
            mask = mask_gdf.to_crs(src.crs)
            
            # Get window from geometry
            from rasterio.mask import mask as rio_mask
            out_image, out_transform = rio_mask(src, mask.geometry, crop=True, nodata=src.nodata)
            
            # Flatten and count unique values (classes)
            data = out_image[0].flatten()
            data = data[data != src.nodata] if src.nodata is not None else data
            
            if len(data) == 0:
                return pd.DataFrame()
            
            unique, counts = np.unique(data, return_counts=True)
            pixel_area = abs(out_transform[0] * out_transform[4])  # m² per pixel
            
            df = pd.DataFrame({
                'class_value': unique,
                'pixel_count': counts,
                'area_m2': counts * pixel_area,
                'area_ha': counts * pixel_area / 10000,
                'area_km2': counts * pixel_area / 1_000_000
            })
            return df.sort_values('area_ha', ascending=False)
    except Exception as e:
        st.error(f"Zonal stats failed: {e}")
        return pd.DataFrame()


def query_arcgis_rest_service(url: str, bbox: Tuple[float, float, float, float]) -> Optional[gpd.GeoDataFrame]:
    """
    Query an ArcGIS REST service (FeatureServer or MapServer) within a bounding box.
    Tries to return features as GeoJSON.
    """
    try:
        minx, miny, maxx, maxy = bbox
        
        params = {
            'where': '1=1',
            'geometry': f"{minx},{miny},{maxx},{maxy}",
            'geometryType': 'esriGeometryEnvelope',
            'inSR': '4326',
            'spatialRel': 'esriSpatialRelIntersects',
            'outFields': '*',
            'returnGeometry': 'true',
            'f': 'geojson',
            'resultRecordCount': 1000
        }
        
        # Try appending /query if not already present
        if '/query' not in url.lower():
            query_url = url.rstrip('/') + '/query'
        else:
            query_url = url
        response = requests.get(query_url, params=params, timeout=30)
        
        if response.status_code == 200:
            try:
                gdf = gpd.read_file(io.StringIO(response.text))
                if not gdf.empty:
                    if gdf.crs is None:
                        gdf.set_crs(epsg=4326, inplace=True)
                    elif gdf.crs.to_epsg() != 4326:
                        gdf = gdf.to_crs(epsg=4326)
                    return gdf
                else:
                    st.warning("No features found in the current AOI.")
                    return None
            except Exception as e:
                st.error(f"Failed to parse response: {e}")
                return None
        else:
            if response.status_code == 400:
                st.error("ArcGIS server returned Status 400 (Bad Request).")
                st.info(
                    "**Most likely cause:** You entered the root services folder instead of a specific layer.\n\n"
                    "Your URL: `https://kgis.ksrsac.in/kgismaps2/rest/services`\n\n"
                    "**Correct format:**\n"
                    "Go to the ArcGIS REST directory → Browse into a service → Choose a MapServer or FeatureServer → Pick a layer number (e.g. `/0`, `/1`).\n\n"
                    "**Example of a valid URL:**\n"
                    "`https://kgis.ksrsac.in/kgismaps2/rest/services/SomeService/MapServer/0`\n\n"
                    "Then paste that full layer URL here."
                )
            else:
                st.error(f"ArcGIS server error: Status {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Error querying ArcGIS REST: {e}")
        return None

def delineate_watershed(
    dem_path: str, 
    outlet_lon: float, 
    outlet_lat: float,
    snap_threshold: int = 1000,
    fill_depressions: bool = True
) -> Tuple[Optional[gpd.GeoDataFrame], Optional[dict]]:
    """
    Main watershed delineation using pysheds.
    Returns (watershed_gdf, stats_dict)
    """
    if not HAS_PYSHEDS:
        st.error("pysheds is not installed. Please run: pip install pysheds")
        return None, None

    try:
        grid = Grid.from_raster(dem_path)
        dem = grid.read_raster(dem_path)

        # 1. Fill pits / depressions
        if fill_depressions:
            dem = grid.fill_pits(dem)
            dem = grid.fill_depressions(dem)
            dem = grid.resolve_flats(dem)

        # 2. Flow direction (D8)
        fdir = grid.flowdir(dem)

        # 3. Flow accumulation
        acc = grid.accumulation(fdir)

        # 4. Snap outlet to high accumulation cell
        x_snap, y_snap = grid.snap_to_mask(acc > snap_threshold, (outlet_lon, outlet_lat), xytype='coordinate')

        # 5. Delineate catchment
        catchment = grid.catchment(
            x=x_snap, 
            y=y_snap, 
            fdir=fdir, 
            xytype='coordinate',
            recursionlimit=15000
        )

        # 6. Vectorize
        watershed_gdf = vectorize_watershed(grid, catchment, crs="EPSG:4326")

        if watershed_gdf is None or watershed_gdf.empty:
            return None, None

        # Calculate stats (with fallback for missing EPSG:54009)
        try:
            area_ha = watershed_gdf.to_crs(epsg=54009).geometry.area.sum() / 10000
        except Exception:
            # Fallback to Web Mercator if Mollweide is not available
            area_ha = watershed_gdf.to_crs(epsg=3857).geometry.area.sum() / 10000

        stats = {
            'outlet_original': (outlet_lon, outlet_lat),
            'outlet_snapped': (x_snap, y_snap),
            'area_ha': round(area_ha, 2),
            'area_km2': round(area_ha / 100, 2),
            'snap_threshold': snap_threshold
        }

        return watershed_gdf, stats

    except AttributeError as e:
        if "in1d" in str(e):
            st.error("Watershed delineation failed due to a NumPy compatibility issue.")
            st.warning(
                "**Fix:**\n"
                "Run this command in your terminal:\n"
                "```bash\n"
                "pip install 'numpy<2.0'\n"
                "```\n\n"
                "`pysheds` currently does not support NumPy 2.0+. "
                "Downgrading NumPy should fix this immediately."
            )
        else:
            st.error(f"Watershed delineation failed: {str(e)}")
        return None, None

    except Exception as e:
        st.error(f"Watershed delineation failed: {str(e)}")
        return None, None

# ==================== FOLIUM MAP (ENHANCED) ====================

def create_folium_map(
    aoi_gdf: Optional[gpd.GeoDataFrame] = None,
    gdf_osm: Optional[gpd.GeoDataFrame] = None,
    gdf_custom: Optional[gpd.GeoDataFrame] = None,
    custom_class_col: Optional[str] = None,
    watershed_gdf: Optional[gpd.GeoDataFrame] = None,
    arcgis_layers: Optional[Dict[str, gpd.GeoDataFrame]] = None,
    center: Optional[Tuple[float, float]] = None,
    zoom: int = 12
) -> folium.Map:
    """Create the interactive map with all layers including watershed."""
    
    if center is None:
        if aoi_gdf is not None and not aoi_gdf.empty:
            centroid = aoi_gdf.geometry.centroid.iloc[0]
            center = (centroid.y, centroid.x)
        else:
            center = (12.87, 74.88)  # Mangalore default

    m = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles="OpenStreetMap",
        control_scale=True,
        prefer_canvas=True
    )

    # AOI Boundary
    if aoi_gdf is not None and not aoi_gdf.empty:
        folium.GeoJson(
            aoi_gdf,
            name="Area of Interest",
            style_function=lambda x: {'fillColor': 'none', 'color': '#d62728', 'weight': 3, 'dashArray': '5, 5'},
            tooltip="AOI Boundary"
        ).add_to(m)

    # OSM Land Use
    if gdf_osm is not None and not gdf_osm.empty:
        if 'landuse' not in gdf_osm.columns:
            possible = [c for c in gdf_osm.columns if 'landuse' in c.lower()]
            if possible:
                gdf_osm = gdf_osm.rename(columns={possible[0]: 'landuse'})
            else:
                gdf_osm['landuse'] = 'unknown'

        def osm_style(feature):
            landuse = feature.get('properties', {}).get('landuse', 'default')
            return {
                'fillColor': get_landuse_color(landuse),
                'color': '#333333', 'weight': 0.5, 'fillOpacity': 0.65
            }

        folium.GeoJson(
            gdf_osm,
            name="OSM Land Use",
            style_function=osm_style,
            tooltip=folium.GeoJsonTooltip(fields=['landuse'], aliases=['Land Use:']),
        ).add_to(m)

    # Custom Shapefile
    if gdf_custom is not None and not gdf_custom.empty and custom_class_col:
        if custom_class_col in gdf_custom.columns:
            unique_vals = gdf_custom[custom_class_col].dropna().unique().tolist()
            color_map = generate_categorical_colors(unique_vals)

            def custom_style(feature):
                val = str(feature.get('properties', {}).get(custom_class_col, 'Unknown'))
                return {
                    'fillColor': color_map.get(val, '#9e9e9e'),
                    'color': '#222222', 'weight': 0.4, 'fillOpacity': 0.7
                }

            folium.GeoJson(
                gdf_custom,
                name=f"Custom: {custom_class_col}",
                style_function=custom_style,
                tooltip=folium.GeoJsonTooltip(fields=[custom_class_col]),
            ).add_to(m)

    # Watershed Layer (NEW)
    if watershed_gdf is not None and not watershed_gdf.empty:
        folium.GeoJson(
            watershed_gdf,
            name="Delineated Watershed",
            style_function=lambda x: {
                'fillColor': '#3182bd',
                'color': '#08519c',
                'weight': 2.5,
                'fillOpacity': 0.35,
                'dashArray': '3, 3'
            },
            tooltip="Watershed Boundary",
            popup=folium.Popup("Delineated Watershed", max_width=200)
        ).add_to(m)

    # ArcGIS REST Layers with per-class coloring support
    if arcgis_layers:
        for name, layer_info in arcgis_layers.items():
            gdf = layer_info.get("gdf") if isinstance(layer_info, dict) else layer_info
            color_by = layer_info.get("color_by") if isinstance(layer_info, dict) else None

            if gdf is not None and not gdf.empty:
                if color_by and color_by in gdf.columns:
                    # Per-class coloring
                    unique_values = gdf[color_by].dropna().unique().tolist()
                    color_map = generate_categorical_colors(unique_values)

                    def make_style_function(cmap):
                        def style_func(feature):
                            val = feature.get('properties', {}).get(color_by)
                            return {
                                'fillColor': cmap.get(val, '#9e9e9e'),
                                'color': '#333333',
                                'weight': 0.8,
                                'fillOpacity': 0.65
                            }
                        return style_func

                    folium.GeoJson(
                        gdf,
                        name=f"{name} (by {color_by})",
                        style_function=make_style_function(color_map),
                        tooltip=folium.GeoJsonTooltip(fields=[color_by] + [c for c in gdf.columns if c != color_by][:3])
                    ).add_to(m)
                else:
                    # Default single color
                    folium.GeoJson(
                        gdf,
                        name=name,
                        style_function=lambda x: {
                            'fillColor': '#ff7f00',
                            'color': '#d35400',
                            'weight': 1,
                            'fillOpacity': 0.5
                        },
                        tooltip=folium.GeoJsonTooltip(fields=list(gdf.columns)[:5])
                    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    folium.plugins.Fullscreen().add_to(m)

    return m

# ==================== SESSION STATE ====================
def init_session_state():
    defaults = {
        'gdf_osm': None,
        'gdf_custom': None,
        'aoi_gdf': None,
        'custom_class_col': None,
        'osm_stats': None,
        'custom_stats': None,
        'last_place': None,
        # Watershed new states
        'dem_path': None,
        'watershed_gdf': None,
        'watershed_stats': None,
        'outlet_lon': None,
        'outlet_lat': None,
        # Raster land cover layers (user uploaded or from GEE)
        'raster_layers': {},   # {layer_name: {"path": str, "crs": str, "classes": dict}}
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

init_session_state()

# ==================== UI ====================
st.markdown('<p class="main-header">🗺️ Land Use Map Maker + Watershed Tools</p>', unsafe_allow_html=True)
st.markdown(f'<p class="sub-header">v{APP_VERSION} • OSM • Rasters • Watersheds • ArcGIS REST</p>', unsafe_allow_html=True)

# SIDEBAR
with st.sidebar:
    st.header("📍 Area of Interest")
    
    aoi_method = st.radio("Define AOI", ["Place Name", "Bounding Box"], horizontal=True)
    
    if aoi_method == "Place Name":
        place_name = st.text_input("Place name", value="Mangalore, Karnataka, India")
        if st.button("🔍 Geocode AOI"):
            with st.spinner("Geocoding..."):
                try:
                    aoi = ox.geocode_to_gdf(place_name)
                    st.session_state.aoi_gdf = aoi
                    st.session_state.last_place = place_name
                    st.success("AOI set!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Geocoding failed: {e}")
    else:
        c1, c2 = st.columns(2)
        with c1:
            north = st.number_input("North", value=13.0, step=0.01, format="%.4f")
            south = st.number_input("South", value=12.7, step=0.01, format="%.4f")
        with c2:
            east = st.number_input("East", value=75.0, step=0.01, format="%.4f")
            west = st.number_input("West", value=74.7, step=0.01, format="%.4f")
        if st.button("Set Bounding Box"):
            try:
                geom = box(west, south, east, north)
                st.session_state.aoi_gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
                st.success("AOI set!")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    st.divider()

    # === LAND USE SECTION (existing) ===
    st.header("🌍 OSM Land Use")
    fetch_all = st.checkbox("Fetch all landuse features", value=True)
    
    if st.button("⬇️ Fetch OSM Land Use", type="primary"):
        if st.session_state.aoi_gdf is None:
            st.error("Define AOI first")
        else:
            with st.spinner("Downloading from OSM..."):
                try:
                    minx, miny, maxx, maxy = st.session_state.aoi_gdf.total_bounds
                    tags = {"landuse": True} if fetch_all else {"landuse": ['residential','commercial','industrial','forest','farmland']}
                    # osmnx v2+ syntax: bbox order is (west, south, east, north)
                    gdf = ox.features_from_bbox(bbox=(minx, miny, maxx, maxy), tags=tags)
                    if gdf is not None and not gdf.empty:
                        gdf = gdf[gdf.geometry.type.isin(['Polygon', 'MultiPolygon'])].reset_index(drop=True)
                        st.session_state.gdf_osm = gdf
                        st.session_state.osm_stats = calculate_area_stats(gdf, 'landuse', "OSM")
                        st.success(f"Downloaded {len(gdf)} features")
                    else:
                        st.warning("No landuse features found")
                except Exception as e:
                    st.error(f"OSM error: {e}")

    st.divider()

    # === CUSTOM SHAPEFILE (existing) ===
    st.header("📁 Custom Shapefile")
    uploaded = st.file_uploader("Upload .zip or .shp", type=['zip','shp'])
    if uploaded and st.button("Load Custom Layer"):
        # (same handling as before - abbreviated for space)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                if uploaded.name.endswith('.zip'):
                    with zipfile.ZipFile(uploaded, 'r') as z: z.extractall(tmp)
                    shp = [f for f in os.listdir(tmp) if f.endswith('.shp')][0]
                    gdf = gpd.read_file(os.path.join(tmp, shp))
                else:
                    gdf = gpd.read_file(uploaded)
                if gdf.crs is None: gdf.set_crs(4326, inplace=True)
                elif gdf.crs.to_epsg() != 4326: gdf = gdf.to_crs(4326)
                st.session_state.gdf_custom = gdf
                st.success(f"Loaded {len(gdf)} features")
                st.rerun()
        except Exception as e:
            st.error(str(e))

    if st.session_state.gdf_custom is not None:
        gdf_c = st.session_state.gdf_custom
        possible_cols = [c for c in gdf_c.columns if gdf_c[c].dtype == 'object' or gdf_c[c].nunique() < 50]
        class_col = st.selectbox("Class column", options=gdf_c.columns, index=0)
        st.session_state.custom_class_col = class_col
        if st.button("Update Custom Styling"):
            st.session_state.custom_stats = calculate_area_stats(gdf_c, class_col, "Custom")
            st.rerun()

    st.divider()

    # ==================== NEW: WATERSHED SECTION ====================
    st.header("🌊 Watershed Delineation")
    
    if not HAS_PYSHEDS:
        st.error("`pysheds` is required for watershed delineation. Install with: `pip install pysheds rasterio`")
    if not HAS_ELEVATION:
        st.info("`elevation` package not found — auto-download disabled. You can still upload your own DEM below.")
    
    # Step 1: Download DEM
    if st.session_state.aoi_gdf is not None:
        dem_product = st.selectbox("DEM Product", ["SRTMGL3 (90m - faster)", "SRTMGL1 (30m - finer)"], index=0)
        product_code = "SRTMGL3" if "90m" in dem_product else "SRTMGL1"
        
        if st.button("⬇️ Try Auto-Download SRTM (via OpenTopography)"):
            with st.spinner("Downloading DEM from OpenTopography API..."):
                # Map old product names to bmi-topography dem_type
                dem_type = "SRTMGL3" if "SRTM3" in product_code else "SRTMGL1"
                dem_path = download_dem_bmi(st.session_state.aoi_gdf, dem_type=dem_type)
                if dem_path:
                    st.session_state.dem_path = dem_path
                    st.success(f"DEM downloaded: {os.path.basename(dem_path)}")
                    st.rerun()
    else:
        st.info("Define an AOI first to download DEM")

    # === Upload your own DEM (Strongly Recommended) ===
    st.markdown("### 📤 Upload your own DEM")
    st.caption("**Recommended method** — especially on Windows. The auto-download often fails due to missing system tools.")
    dem_upload = st.file_uploader(
        "Upload DEM (.tif / .tiff)",
        type=['tif', 'tiff'],
        key="dem_uploader"
    )
    if dem_upload is not None:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".tif") as tmp:
                tmp.write(dem_upload.getbuffer())
                st.session_state.dem_path = tmp.name
            st.success(f"✅ Custom DEM loaded: {dem_upload.name}")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to load DEM: {e}")

    # DEM status
    if st.session_state.dem_path and os.path.exists(st.session_state.dem_path):
        st.success(f"✅ DEM ready: {os.path.basename(st.session_state.dem_path)}")

        col_dem1, col_dem2 = st.columns(2)
        with col_dem1:
            # Download raw DEM
            with open(st.session_state.dem_path, "rb") as f:
                dem_bytes = f.read()
            st.download_button(
                label="⬇️ Download Raw DEM (.tif)",
                data=dem_bytes,
                file_name=os.path.basename(st.session_state.dem_path),
                mime="image/tiff",
                key="download_dem"
            )
        with col_dem2:
            if st.button("🗻 View DEM in 3D (Plotly)", key="view_3d_dem"):
                st.session_state.show_3d_dem = True

        # 3D DEM Visualization using Plotly
        if st.session_state.get("show_3d_dem"):
            try:
                import plotly.graph_objects as go
                with rasterio.open(st.session_state.dem_path) as src:
                    dem_array = src.read(1)
                    # Downsample for performance if too large
                    if dem_array.shape[0] > 500 or dem_array.shape[1] > 500:
                        dem_array = dem_array[::max(1, dem_array.shape[0]//400), ::max(1, dem_array.shape[1]//400)]
                    
                    fig = go.Figure(data=[go.Surface(z=dem_array, colorscale='Earth')])
                    fig.update_layout(
                        title="3D DEM Visualization",
                        autosize=True,
                        width=700,
                        height=600,
                        margin=dict(l=65, r=50, b=65, t=90)
                    )
                    st.plotly_chart(fig, use_container_width=True)
            except Exception as e:
                st.error(f"Failed to render 3D DEM: {e}")
        
        # Step 2: Select Outlet
        st.markdown("**Select Outlet / Pour Point**")
        
        use_click = st.checkbox("Click on map below to choose outlet point", value=True)
        
        col_lat, col_lon = st.columns(2)
        with col_lat:
            manual_lat = st.number_input("Outlet Latitude", value=12.85, format="%.5f", step=0.0001)
        with col_lon:
            manual_lon = st.number_input("Outlet Longitude", value=74.85, format="%.5f", step=0.0001)

        if use_click:
            st.caption("👆 After clicking on the map, the coordinates will appear below. Then click 'Use Clicked Point'.")
        
        if st.button("📍 Use Clicked Point as Outlet"):
            # This will be populated from the map interaction below
            if st.session_state.get('last_clicked_lat') and st.session_state.get('last_clicked_lon'):
                st.session_state.outlet_lat = st.session_state['last_clicked_lat']
                st.session_state.outlet_lon = st.session_state['last_clicked_lon']
                st.success(f"Outlet set to: {st.session_state.outlet_lat:.5f}, {st.session_state.outlet_lon:.5f}")
            else:
                st.warning("No click detected yet. Click on the map first.")

        # Allow manual override
        if st.button("Set Manual Outlet"):
            st.session_state.outlet_lat = manual_lat
            st.session_state.outlet_lon = manual_lon
            st.success("Manual outlet set.")

        # Step 3: Delineate
        snap_thresh = st.slider("Snap threshold (flow accumulation)", 500, 5000, 1000, step=100)
        
        if st.button("🌊 DELINEATE WATERSHED", type="primary"):
            if st.session_state.outlet_lat is None or st.session_state.outlet_lon is None:
                st.error("Please set an outlet point first (click map or manual).")
            elif st.session_state.dem_path is None:
                st.error("No DEM loaded.")
            else:
                with st.spinner("Delineating watershed with pysheds... (can take 10-60s)"):
                    ws_gdf, ws_stats = delineate_watershed(
                        st.session_state.dem_path,
                        st.session_state.outlet_lon,
                        st.session_state.outlet_lat,
                        snap_threshold=snap_thresh
                    )
                    if ws_gdf is not None:
                        st.session_state.watershed_gdf = ws_gdf
                        st.session_state.watershed_stats = ws_stats
                        st.success(f"Watershed delineated! Area: {ws_stats['area_km2']} km²")
                        st.rerun()
                    else:
                        st.error("Delineation failed. Try a different outlet or lower snap threshold.")

    # Watershed status
    if st.session_state.watershed_gdf is not None:
        st.success("✅ Watershed ready on map")
        if st.session_state.watershed_stats:
            st.json(st.session_state.watershed_stats)

        # Download watershed with format choice
        ws_gdf = st.session_state.watershed_gdf
        ws_format = st.selectbox("Watershed Format", ["GeoJSON", "Shapefile (.zip)"], key="ws_fmt")
        
        if ws_format == "GeoJSON":
            ws_data = ws_gdf.to_json()
            ws_fname = "watershed.geojson"
            ws_mime = "application/json"
        else:
            ws_data = gdf_to_shapefile_zip(ws_gdf, layer_name="watershed")
            ws_fname = "watershed.zip"
            ws_mime = "application/zip"
        
        st.download_button(
            label=f"⬇️ Download Watershed ({ws_format})",
            data=ws_data,
            file_name=ws_fname,
            mime=ws_mime,
            key="download_watershed"
        )

    # Clear watershed data
    if st.button("🗑️ Clear Watershed Data"):
        st.session_state.watershed_gdf = None
        st.session_state.watershed_stats = None
        st.session_state.dem_path = None
        st.session_state.outlet_lat = None
        st.session_state.outlet_lon = None
        st.rerun()

    st.divider()

    # ==================== NEW: LAND COVER RASTERS SECTION ====================
    st.header("🛰️ Land Cover Rasters")
    st.caption("Upload classified rasters (ESA WorldCover, ESRI, your own, or from GEE)")

    raster_upload = st.file_uploader(
        "Upload classified GeoTIFF",
        type=['tif', 'tiff'],
        key="raster_uploader",
        help="Integer raster where each value represents a land cover class"
    )

    if raster_upload is not None:
        raster_name = st.text_input("Give this layer a short name", value=raster_upload.name.split('.')[0])
        
        if st.button("📥 Add Raster Layer", type="primary"):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tif") as tmp:
                    tmp.write(raster_upload.getbuffer())
                    tmp_path = tmp.name
                
                # Basic metadata
                with rasterio.open(tmp_path) as src:
                    unique_classes = np.unique(src.read(1))
                    unique_classes = unique_classes[unique_classes != src.nodata] if src.nodata is not None else unique_classes
                
                st.session_state.raster_layers[raster_name] = {
                    "path": tmp_path,
                    "classes": {int(c): f"Class_{int(c)}" for c in unique_classes[:50]}  # limit for display
                }
                st.success(f"Added '{raster_name}' with {len(unique_classes)} classes")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to load raster: {e}")

    # Show currently loaded rasters
    if st.session_state.raster_layers:
        st.markdown("**Loaded Raster Layers:**")
        for name, info in st.session_state.raster_layers.items():
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"• **{name}** ({len(info['classes'])} classes)")
            with col2:
                if st.button("🗑️", key=f"del_{name}"):
                    del st.session_state.raster_layers[name]
                    st.rerun()

    # Auto-compute zonal stats if watershed exists
    if st.session_state.watershed_gdf is not None and st.session_state.raster_layers:
        if st.button("📊 Compute Land Cover Stats Inside Watershed"):
            watershed = st.session_state.watershed_gdf
            all_stats = []
            
            for name, info in st.session_state.raster_layers.items():
                stats_df = compute_zonal_stats_from_raster(info["path"], watershed)
                if not stats_df.empty:
                    stats_df["layer"] = name
                    all_stats.append(stats_df)
            
            if all_stats:
                combined = pd.concat(all_stats, ignore_index=True)
                st.session_state.raster_zonal_stats = combined
                st.success("Zonal statistics computed!")
            else:
                st.warning("Could not compute stats for the loaded rasters.")

    st.divider()

    # ==================== ARCGIS REST API ====================
    st.header("🌐 ArcGIS REST API Layer")
    st.caption("Load vector data from ArcGIS REST services (FeatureServer or MapServer) within your AOI")

    arcgis_url = st.text_input(
        "ArcGIS REST URL (FeatureServer or MapServer)",
        placeholder="https://.../arcgis/rest/services/YourService/FeatureServer/0 or /MapServer/0",
        help="Works with both FeatureServer and many MapServer layers that support GeoJSON output"
    )

    if st.button("📥 Load from ArcGIS REST", disabled=not arcgis_url):
        if st.session_state.aoi_gdf is not None:
            with st.spinner("Querying ArcGIS REST service..."):
                bbox = tuple(st.session_state.aoi_gdf.total_bounds)
                gdf = query_arcgis_rest_service(arcgis_url, bbox)
                
                if gdf is not None and not gdf.empty:
                    # Store it
                    layer_name = f"ArcGIS_{len(st.session_state.get('arcgis_layers', {})) + 1}"
                    if 'arcgis_layers' not in st.session_state:
                        st.session_state.arcgis_layers = {}
                    st.session_state.arcgis_layers[layer_name] = gdf
                    st.success(f"Loaded {len(gdf)} features from ArcGIS layer!")
                else:
                    st.warning("No data returned or query failed.")
        else:
            st.error("Please define an Area of Interest first.")

    # Show loaded ArcGIS layers with per-class coloring, download, and attributes
    if st.session_state.get('arcgis_layers'):
        # Quick clip all ArcGIS layers to AOI
        if st.button("✂️ Clip All ArcGIS Layers to Current AOI"):
            if st.session_state.aoi_gdf is not None:
                aoi_geom = st.session_state.aoi_gdf.geometry.iloc[0]
                for name in list(st.session_state.arcgis_layers.keys()):
                    try:
                        st.session_state.arcgis_layers[name]["gdf"] = gpd.clip(
                            st.session_state.arcgis_layers[name]["gdf"], aoi_geom
                        )
                    except Exception as e:
                        st.warning(f"Failed to clip {name}: {e}")
                st.success("Clipped all ArcGIS layers to AOI!")
                st.rerun()
            else:
                st.error("No AOI defined.")

        st.markdown("**Loaded ArcGIS Layers:**")
        for name, layer_data in list(st.session_state.arcgis_layers.items()):
            # Support both old (raw gdf) and new (dict with metadata) storage
            if isinstance(layer_data, dict):
                gdf = layer_data.get("gdf", layer_data)
                color_by = layer_data.get("color_by")
            else:
                gdf = layer_data
                color_by = None
                # Migrate old format to new format
                st.session_state.arcgis_layers[name] = {"gdf": gdf, "color_by": None}
            
            col1, col2 = st.columns([4, 2])
            with col1:
                st.write(f"• **{name}** ({len(gdf)} features)")
            with col2:
                # Color by column selector
                columns = [c for c in gdf.columns if c != 'geometry']
                current_color_by = color_by
                
                color_by = st.selectbox(
                    "Color by",
                    options=["None"] + columns,
                    index=0 if current_color_by is None else columns.index(current_color_by) + 1,
                    key=f"color_by_{name}"
                )
                
                if color_by == "None":
                    st.session_state.arcgis_layers[name]["color_by"] = None
                else:
                    st.session_state.arcgis_layers[name]["color_by"] = color_by

            # Download section
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                download_format = st.selectbox(
                    "Format", ["GeoJSON", "Shapefile (.zip)"], 
                    key=f"fmt_{name}", label_visibility="collapsed"
                )
                if download_format == "GeoJSON":
                    data = gdf.to_json()
                    fname = f"{name}.geojson"
                    mime = "application/json"
                else:
                    data = gdf_to_shapefile_zip(gdf, layer_name=name)
                    fname = f"{name}.zip"
                    mime = "application/zip"
                
                st.download_button("⬇️ Download", data=data, file_name=fname, mime=mime, key=f"dl_{name}")

            with col_d2:
                if st.button("👁️ Attributes", key=f"attr_{name}"):
                    st.session_state[f"show_attr_{name}"] = not st.session_state.get(f"show_attr_{name}", False)

            if st.session_state.get(f"show_attr_{name}"):
                st.dataframe(gdf.drop(columns='geometry', errors='ignore'), use_container_width=True, height=180)

            # Show attribute table if toggled
            if st.session_state.get(f"show_attr_{name}"):
                st.dataframe(gdf.drop(columns='geometry', errors='ignore'), use_container_width=True, height=200)

    st.divider()

    # ==================== SUPPORT ====================
    st.markdown("---")
    st.markdown("**Like this tool?**")

    col1, col2 = st.columns(2)
    with col1:
        st.link_button(
            "☕ Support on Ko-fi",
            "https://ko-fi.com/jayakrishnash001",
            help="Support continued development of this tool"
        )
    with col2:
        if st.button("💡 Suggest feature"):
            st.info("Message me your ideas! I'm happy to keep improving this.")

    if st.button("🗑️ Clear ALL Data"):
        keys_to_delete = []
        for key in list(st.session_state.keys()):
            if key.startswith(('gdf_', 'aoi', 'custom', 'osm', 'dem', 'watershed', 'outlet', 'raster', 'arcgis')):
                # Don't directly set widget keys to None - delete them instead
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            try:
                del st.session_state[key]
            except KeyError:
                pass
        st.rerun()

# ==================== MAIN MAP + STATS ====================
col_map, col_stats = st.columns([2.3, 1])

with col_map:
    st.subheader("🗺️ Interactive Map")
    
    # Get last clicked from map for outlet selection
    map_data = st_folium(
        create_folium_map(
            aoi_gdf=st.session_state.aoi_gdf,
            gdf_osm=st.session_state.gdf_osm,
            gdf_custom=st.session_state.gdf_custom,
            custom_class_col=st.session_state.custom_class_col,
            watershed_gdf=st.session_state.watershed_gdf,
            arcgis_layers=st.session_state.get('arcgis_layers'),
        ),
        width=None,
        height=620,
        returned_objects=["last_clicked"]
    )
    
    if map_data and map_data.get("last_clicked"):
        clicked = map_data["last_clicked"]
        st.session_state['last_clicked_lat'] = clicked["lat"]
        st.session_state['last_clicked_lon'] = clicked["lng"]
        st.caption(f"📍 Last map click: {clicked['lat']:.5f}, {clicked['lng']:.5f} (use in sidebar)")

with col_stats:
    st.subheader("📊 Statistics")
    
    # Land Use Stats (existing logic abbreviated)
    if st.session_state.osm_stats is not None and not st.session_state.osm_stats.empty:
        st.markdown("**OSM Land Use**")
        st.dataframe(st.session_state.osm_stats.style.format({'area_ha':'{:,.1f}', 'area_km2':'{:,.2f}'}), 
                     use_container_width=True, hide_index=True)
    
    if st.session_state.custom_stats is not None and not st.session_state.custom_stats.empty:
        st.markdown("**Custom Layer**")
        st.dataframe(st.session_state.custom_stats.style.format({'area_ha':'{:,.1f}', 'area_km2':'{:,.2f}'}),
                     use_container_width=True, hide_index=True)

    if st.session_state.watershed_stats:
        st.markdown("**Watershed**")
        ws = st.session_state.watershed_stats
        st.metric("Watershed Area", f"{ws.get('area_km2', 0)} km²")
        st.caption(f"Snapped outlet: {ws.get('outlet_snapped', 'N/A')}")

    # Raster Zonal Stats (new)
    if st.session_state.get('raster_zonal_stats') is not None:
        st.markdown("**Land Cover inside Watershed (from Rasters)**")
        raster_stats = st.session_state.raster_zonal_stats
        st.dataframe(
            raster_stats.style.format({
                'area_ha': '{:,.1f}',
                'area_km2': '{:,.2f}'
            }),
            use_container_width=True,
            hide_index=True
        )

# ==================== EXPORT SECTION ====================
st.divider()
st.subheader("💾 Export")

e1, e2, e3 = st.columns(3)

with e1:
    if st.button("Download Map (HTML)"):
        m = create_folium_map(
            aoi_gdf=st.session_state.aoi_gdf,
            gdf_osm=st.session_state.gdf_osm,
            gdf_custom=st.session_state.gdf_custom,
            custom_class_col=st.session_state.custom_class_col,
            watershed_gdf=st.session_state.watershed_gdf,
            arcgis_layers=st.session_state.get('arcgis_layers')
        )
        st.download_button("Download HTML", m._repr_html_(), "landuse_watershed_map.html", "text/html")

with e2:
    if st.button("Download All Layers (GeoJSON)"):
        layers = []
        for name, gdf in [("aoi", st.session_state.aoi_gdf), 
                          ("osm", st.session_state.gdf_osm),
                          ("custom", st.session_state.gdf_custom),
                          ("watershed", st.session_state.watershed_gdf)]:
            if gdf is not None and not gdf.empty:
                g = gdf.copy()
                g['layer'] = name
                layers.append(g)
        if layers:
            combined = pd.concat(layers, ignore_index=True)
            st.download_button("Download GeoJSON", combined.to_json(), "all_layers.geojson", "application/json")

with e3:
    if st.button("Download Watershed Only"):
        if st.session_state.watershed_gdf is not None:
            st.download_button(
                "Download Watershed GeoJSON",
                st.session_state.watershed_gdf.to_json(),
                "watershed.geojson",
                "application/json"
            )
        else:
            st.warning("No watershed to export")

# Footer
st.divider()
with st.expander("ℹ️ How to use Watershed Delineation"):
    st.markdown("""
    **Workflow:**
    1. Set your Area of Interest (place name or bounding box)
    2. (Optional) Load OSM land use and/or custom shapefiles first
    3. Go to **Watershed Delineation** section in sidebar
    4. Download SRTM DEM (90m is faster, 30m is more detailed)
    5. **Click on the map** to choose an outlet point (river mouth, dam, monitoring station, etc.)
    6. Adjust snap threshold if needed (higher = snaps to bigger streams)
    7. Click **DELINEATE WATERSHED**
    8. The watershed boundary appears on the map (blue hatched)
    
    **Tips for your research:**
    - Great for defining exact catchment boundaries for PPCP sampling or modeling
    - Combine with land use layers to calculate % urban / agricultural / forest inside the watershed
    - Export the watershed + land use layers together for QGIS / R analysis
    
    **Limitations:**
    - SRTM has voids in some steep/mountainous areas (though rare in coastal Karnataka)
    - pysheds uses D8 flow direction — good for most purposes but not as advanced as TopoToolbox or SAGA
    - Very large AOIs can be slow
    """)

st.caption("Built for environmental researchers • OSM © OpenStreetMap contributors • DEM from NASA SRTM • pysheds for hydrology")