<<<<<<< HEAD
# Small-Mapmaker
Helps download maps for you. It can also visualise data for you in a small window. This app can also do watershed delineation. Try this!
=======
# Land Use Map Maker + Watershed Delineation

**Current Version:** `0.4.0`

An interactive Streamlit web application to create, visualize, and analyze land use maps by combining:

- **OpenStreetMap (OSM)** land use data (via `osmnx`)
- **Custom shapefiles** uploaded by the user (e.g. your own LULC layers from QGIS, government data, or research)

Perfect for environmental researchers, urban planners, EIA consultants, and students working on land use / land cover (LULC), source apportionment, or watershed studies (like Nethravathi catchment).

## Features (v0.4.0)

- Fetch OSM landuse polygons for any place name or custom bounding box
- Upload and overlay your own shapefiles (as .zip)
- Upload multiple classified rasters + compute zonal statistics **inside the watershed**
- Watershed delineation with pysheds (SRTM or your own DEM)
- Google Earth Engine section (authentication + foundation for ESA WorldCover / Dynamic World)
- Interactive Leaflet map with multiple layers
- Support button linked to Ko-fi
- Export options (HTML map, GeoJSON, CSV stats)
- Designed for environmental research and catchment-scale analysis

**Note**: Google Earth Engine integration and advanced raster visualization are still in active development.

## Quick Start (Local)

1. **Create virtual environment** (recommended)
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Linux/Mac
   # or .venv\Scripts\activate on Windows
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   > **Note**: The watershed tools require `pysheds`, `elevation`, and `rasterio`. These are included in `requirements.txt`. If you only want the original land use features, you can skip them, but the watershed section will show a warning.

3. **Run the app**
   ```bash
   streamlit run app.py
   ```

4. Open your browser at `http://localhost:8501`

## How to Use

### 1. Define Area of Interest (AOI)
- **Option A (Recommended):** Enter a place name like:
  - `Mangalore, Karnataka, India`
  - `Udupi`
  - `Nethravathi catchment`
  - `Manipal`
- **Option B:** Enter bounding box coordinates (lat/lon)

### 2. Fetch OSM Land Use
- Click **"Fetch OSM Land Use Data"**
- It will download polygons tagged with `landuse=*` from OpenStreetMap
- Common classes: residential, commercial, industrial, forest, farmland, water, meadow, etc.

### 3. Add Your Custom Shapefile
- Prepare a **.zip** file containing at minimum: `.shp`, `.shx`, `.dbf`, `.prj`
- Upload the zip
- Select the attribute column that contains land use / class information
- The app will style it automatically

### 4. Explore & Analyze
- Toggle layers on/off
- Click features for attribute popups
- View area statistics and charts in the sidebar / below map
- Adjust opacity if needed (future enhancement)

### 5. Export
- **Interactive HTML map** (best for sharing)
- **Combined GeoJSON** (for QGIS / further analysis)
- **Statistics CSV**

## Tips for Best Results

- Start with smaller areas (city or catchment scale) — large regions can be slow or hit OSM query limits.
- Make sure your custom shapefile is in a projected CRS or geographic (WGS84). The app reprojects automatically to EPSG:4326 for mapping.
- For Indian data: Many state portals or Bhuvan/NRSC provide good LULC shapefiles.
- If you have a specific land use classification system (e.g. NRSC Level I/II), you can map your classes to standard colors in future versions.

## Example Workflow for Your PhD / Research (including Watersheds + Rasters)

1. Define your study area and delineate the watershed
2. Upload one or more classified rasters (ESA WorldCover 10m, ESRI Land Cover, your own supervised classification, or exports from Google Earth Engine)
3. Click **"Compute Land Cover Stats Inside Watershed"**
4. Instantly see area per class from **every raster layer** inside the exact catchment
5. Compare OSM vector land use vs multiple raster sources in one view
6. Export everything (watershed + stats + layers) for further analysis

This makes it very easy to do source apportionment and land use comparison inside precise hydrological boundaries.

## Limitations & Notes

- OSM data quality varies by region (better in urban India now, but still incomplete in rural areas)
- Large uploads or very large AOIs may be slow (Streamlit memory limits on cloud hosting)
- For public hosting (Streamlit Community Cloud / Hugging Face), large shapefiles and heavy OSM queries can hit resource limits. Local run is recommended for serious work.
- No drawing/editing tools yet (you can add features in QGIS then re-upload)

---

**Support**: There's a **☕ Support on Ko-fi** button in the sidebar if this tool has been useful. Every bit of support helps me continue building and improving these tools.

## Future Enhancements (Possible)

- Drawing tools to create/edit polygons
- Raster (GeoTIFF) support for classified satellite imagery
- Pre-built color schemes for Indian LULC standards (NRSC)
- Integration with your existing Python scripts for source apportionment
- Multi-layer comparison and change detection

## Author & Context

Built as a research tool prototype. Tailored for environmental engineering / geospatial workflows involving OpenStreetMap + custom vector data.

This tool was developed during long nights of thesis writing, data analysis, and the general chaos of a PhD. A very special thank you goes to **Lucky**, my sleek black cat and constant companion. Rescued as a tiny, abandoned kitten who could barely walk, he has grown into a chaotic little spirit who supervises my work from the desk (or by dramatically flopping across the keyboard). His head-butts, dramatic meows, and quiet presence have been one of the few steady things during the final stretch of this journey. Every researcher should be so lucky to have a Lucky.

If you want additional features (specific color legends, more export formats, integration with your existing code, or deployment help), just tell me!

## License

Free for research and personal use. Feel free to extend.

---

**Made with ❤️ for geospatial researchers who hate switching between QGIS, Python, and web maps.**
>>>>>>> 9278849 (app working)
