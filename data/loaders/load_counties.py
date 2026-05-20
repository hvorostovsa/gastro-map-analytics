import geopandas as gpd
from tqdm import tqdm
from sqlalchemy import text
from db import engine
from utils import batch_iter

TARGET_STATE_FIPS = {
    'CA': '06', 'NV': '32', 'ID': '16', 'AZ': '04', 'LA': '22',
    'FL': '12', 'MO': '29', 'TN': '47', 'IN': '18', 'PA': '42'
}

INSERT_COUNTY = text("""
INSERT INTO counties (geoid, name, state, geom)
VALUES (
    :geoid, 
    :name, 
    :state, 
    ST_Transform(ST_SetSRID(ST_GeomFromText(:geom), 4269), 4326)::geography
)
ON CONFLICT (geoid) DO NOTHING
""")


def load_counties(shapefile_path):
    print(f"Reading shapefile: {shapefile_path}")
    gdf = gpd.read_file(shapefile_path)
    
    target_fips = list(TARGET_STATE_FIPS.values())
    gdf = gdf[gdf['STATEFP'].isin(target_fips)]
    
    print(f"Found {len(gdf)} counties in target states")
    
    fips_to_state = {v: k for k, v in TARGET_STATE_FIPS.items()}
    
    records = []
    for _, row in tqdm(gdf.iterrows(), total=len(gdf), desc="Processing counties"):
        state_fips = row['STATEFP']
        state_abbr = fips_to_state.get(state_fips)
        
        geom_wkt = row['geometry'].wkt if row['geometry'] else None
        
        if geom_wkt:
            records.append({
                'geoid': row['GEOID'],
                'name': row['NAME'],
                'state': state_abbr,
                'geom': geom_wkt
            })
    
    print(f"Inserting {len(records)} counties...")
    
    with engine.begin() as conn:
        for batch in tqdm(list(batch_iter(records, 1000)), desc="Inserting counties"):
            conn.execute(INSERT_COUNTY, batch)
    
    print(f"✅ Loaded {len(records)} counties")
    
    with engine.connect() as conn:
        result = conn.execute(text("SELECT state, COUNT(*) FROM counties GROUP BY state ORDER BY state"))
        print("\nLoaded counts by state:")
        for row in result:
            print(f"  {row[0]}: {row[1]} counties")


if __name__ == "__main__":
    load_counties("tl_2025_us_county.shp")