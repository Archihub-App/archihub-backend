import sys
import json
import argparse
import os

# Ensure we can import from the app module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.utils.DatabaseHandler import DatabaseHandler
from app.api.geosystem.models import Polygon

def main():
    parser = argparse.ArgumentParser(description="Upload GeoJSON features to MongoDB 'shapes' collection.")
    parser.add_argument("geojson_path", help="Path to the GeoJSON file")
    parser.add_argument("--type", dest="shape_type", default=None, help="Type of the shape (optional)")
    parser.add_argument("--parent", default=None, help="Parent of the shape (optional)")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.geojson_path):
        print(f"Error: File {args.geojson_path} does not exist.")
        sys.exit(1)
        
    print(f"Reading {args.geojson_path}...")
    with open(args.geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    features = data.get("features", [])
    if not features:
        print("No features found in the GeoJSON.")
        sys.exit(1)
        
    print(f"Found {len(features)} features. Uploading to database...")
    mongodb = DatabaseHandler()
    count = 0
    error_count = 0
    
    for feature in features:
        # Update properties with type and parent if provided
        properties = feature.get("properties", {})
        
        if args.shape_type is not None:
            properties["shape_type"] = args.shape_type
            
        if args.parent is not None:
            properties["parent"] = args.parent
            
        # Clean up null values to be consistent with existing backend logic
        feature["properties"] = {k: v for k, v in properties.items() if v is not None}
        
        try:
            poly = Polygon(**feature)
            mongodb.insert_record("shapes", poly)
            count += 1
        except Exception as e:
            print(f"Error inserting feature: {e}")
            error_count += 1
            
    print(f"Finished! Successfully uploaded {count} features. Errors: {error_count}")

if __name__ == "__main__":
    main()
