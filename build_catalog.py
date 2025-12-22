#!/usr/bin/env python3
"""
LiveBarn Venue Catalog Builder
Downloads complete venue/surface database from LiveBarn API
"""

import requests
import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime

DB_PATH = Path(os.getenv('DB_PATH', '/data/livebarn.db'))
API_URL = 'https://watchapi.livebarn.com/api/v2.0.0/staticdata/venues'

def build_catalog():
    """Download and build local venue catalog"""
    
    print("=" * 70)
    print("  🏒 LiveBarn Venue Catalog Builder")
    print("=" * 70)
    print()
    
    print(f"📡 Fetching venue database from LiveBarn API...")
    print(f"   URL: {API_URL}")
    
    try:
        response = requests.get(API_URL, timeout=30)
        response.raise_for_status()
        
        venues_data = response.json()
        
        print(f"✅ Downloaded {len(venues_data)} venues")
        
    except Exception as e:
        print(f"❌ Failed to fetch venue data: {e}")
        return False
    
    # Create/update database
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Create venues table
    c.execute('''
        CREATE TABLE IF NOT EXISTS venues (
            id INTEGER PRIMARY KEY,
            uuid TEXT UNIQUE,
            name TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            country TEXT,
            latitude REAL,
            longitude REAL,
            time_zone TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    ''')
    
    # Create surfaces table
    c.execute('''
        CREATE TABLE IF NOT EXISTS surfaces (
            id INTEGER PRIMARY KEY,
            uuid TEXT UNIQUE,
            name TEXT,
            venue_id INTEGER,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (venue_id) REFERENCES venues(id)
        )
    ''')
    
    # Create favorites table
    c.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            surface_id INTEGER UNIQUE,
            added_at TEXT,
            notes TEXT,
            FOREIGN KEY (surface_id) REFERENCES surfaces(id)
        )
    ''')
    
    # NEW: Create surface_streams table (Critical Fix)
    c.execute('''
        CREATE TABLE IF NOT EXISTS surface_streams (
            id INTEGER PRIMARY KEY,
            surface_id INTEGER UNIQUE,
            venue_uuid TEXT,
            stream_name TEXT,
            venue_name TEXT,
            surface_name TEXT,
            playlist_url TEXT,
            full_captured_url TEXT,
            captured_at TEXT,
            FOREIGN KEY (surface_id) REFERENCES surfaces(id)
        )
    ''')

    print(f"\n💾 Importing venues into database...")
    
    venues_imported = 0
    surfaces_imported = 0
    
    for venue in venues_data:
        try:
            # Insert/update venue
            # Normalize some fields that differ across regions
            city = venue.get('city')
            # LiveBarn may use different keys for state/province across countries
            state = (
                venue.get('state')
                or venue.get('stateCode')
                or venue.get('state_code')
                or venue.get('province')
                or venue.get('provinceCode')
                or venue.get('stateProvince')
                or venue.get('region')
            )
            postal_code = (
                venue.get('postalCode')
                or venue.get('postal_code')
                or venue.get('zip')
                or venue.get('zipCode')
            )

            c.execute('''
                INSERT OR REPLACE INTO venues (
                    id, uuid, name, address, city, state, postal_code, 
                    country, latitude, longitude, time_zone, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                venue.get('id'),
                venue.get('uuid'),
                venue.get('name'),
                venue.get('address'),
                city,
                state,
                postal_code,
                venue.get('country'),
                venue.get('latitude'),
                venue.get('longitude'),
                venue.get('timeZone'),
                datetime.utcnow().isoformat()
            ))
            
            venues_imported += 1
            
            # Insert/update surfaces for this venue
            surfaces = venue.get('surfaces', [])
            for surface in surfaces:
                c.execute('''
                    INSERT OR REPLACE INTO surfaces (
                        id, uuid, name, venue_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                ''', (
                    surface.get('id'),
                    surface.get('uuid'),
                    surface.get('name'),
                    venue.get('id'),
                    datetime.utcnow().isoformat()
                ))
                
                surfaces_imported += 1
            
        except Exception as e:
            print(f"⚠️  Error importing venue {venue.get('name')}: {e}")
            continue
    
    conn.commit()
    
    # Get statistics
    c.execute('SELECT COUNT(*) FROM venues')
    total_venues = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM surfaces')
    total_surfaces = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM favorites')
    total_favorites = c.fetchone()[0]
    
    # Get stream stats
    try:
        c.execute('SELECT COUNT(*) FROM surface_streams')
        total_streams = c.fetchone()[0]
    except:
        total_streams = 0 # In case the table was just created
        
    conn.close()
    
    print(f"\n✅ Catalog built successfully!")
    print(f"   Venues: {total_venues}")
    print(f"   Surfaces: {total_surfaces}")
    print(f"   Favorites: {total_favorites}")
    print(f"   Streams: {total_streams}")
    print()
    print(f"💾 Database: {DB_PATH}")
    
    return True

if __name__ == '__main__':
    import sys
    
    success = build_catalog()
    
    if success:
        print("\n🎉 You can now:")
        print("   1. Run livebarn_manager.py to browse and manage favorites")
        print("   2. Run auto_refresh.py to capture streams for favorites")
        sys.exit(0)
    else:
        print("\n❌ Catalog build failed")
        sys.exit(1)
