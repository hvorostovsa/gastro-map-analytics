import geopandas as gpd
from tqdm import tqdm
from sqlalchemy import text
from db import engine
from utils import batch_iter


additional_states = ["MD"]

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
    
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT DISTINCT state 
            FROM businesses 
            WHERE state IS NOT NULL AND state != ''
        """))
        db_states = [row[0] for row in result]
        db_states.extend(additional_states)
    
    print(f"States found in businesses: {', '.join(db_states)}")
    
    state_fips_map = {
        '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA',
        '08': 'CO', '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL',
        '13': 'GA', '15': 'HI', '16': 'ID', '17': 'IL', '18': 'IN',
        '19': 'IA', '20': 'KS', '21': 'KY', '22': 'LA', '23': 'ME',
        '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN', '28': 'MS',
        '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH',
        '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND',
        '39': 'OH', '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI',
        '45': 'SC', '46': 'SD', '47': 'TN', '48': 'TX', '49': 'UT',
        '50': 'VT', '51': 'VA', '53': 'WA', '54': 'WV', '55': 'WI',
        '56': 'WY'
    }
    
    target_fips = [fips for fips, state in state_fips_map.items() if state in db_states]
    
    gdf = gdf[gdf['STATEFP'].isin(target_fips)]
    
    print(f"Found {len(gdf)} counties in target states")
    
    records = []
    for _, row in tqdm(gdf.iterrows(), total=len(gdf), desc="Processing counties"):
        state_fips = row['STATEFP']
        state_abbr = state_fips_map.get(state_fips)
        
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
    
    print("\nAssigning counties to businesses... Will take a while.")
    
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE businesses b
            SET county = c.name
            FROM counties c
            WHERE ST_Within(b.geom::geometry, c.geom::geometry)
        """))
    
    print("✅ Done")
    
    return len(records)