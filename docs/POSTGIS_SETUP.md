# Local PostGIS setup

This project can run without PostGIS, but GIS facts stay blank until layers are
loaded. The backend expects a normal PostgreSQL/PostGIS database reachable
through `DATABASE_URL`.

## Option A: Existing PostgreSQL/PostGIS

1. Install PostgreSQL with the PostGIS extension.
2. Create a database, for example `agnps`.
3. Enable PostGIS:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

4. Set `DATABASE_URL` in `backend/.env`:

```text
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/agnps
```

5. Create the app-side tables:

```powershell
psql "postgresql://postgres:postgres@localhost:5432/agnps" -f backend/sql/schema.sql
```

## Option B: Docker later

Docker is not currently available on this PC, but if it is installed later,
`postgis/postgis` is the simplest local dev image:

```powershell
docker run --name agnps-postgis -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=agnps -p 5432:5432 -d postgis/postgis:16-3.4
```

Then use the same `DATABASE_URL` shown above.

## Load the first vector layers

```powershell
cd backend
python scripts/download_data.py --layers counties towns huc8 huc10 huc12 wipwl dac
python scripts/load_layers.py --layer counties --where "STATEFP='36'"
python scripts/load_layers.py --layer towns
python scripts/load_layers.py --layer huc8
python scripts/load_layers.py --layer huc10
python scripts/load_layers.py --layer huc12
python scripts/load_layers.py --layer wipwl
python scripts/load_layers.py --layer dac
python scripts/verify_layers.py
```

SSURGO/gSSURGO is a separate manual step because the official state database is
large and distributed through NRCS/Box/GDG.

## Soil layer after gSSURGO download

After extracting the New York gSSURGO `.gdb` into `data/raw/ssurgo/`:

```powershell
cd backend
python scripts/prepare_ssurgo.py --gdb ..\data\raw\ssurgo\gSSURGO_NY.gdb
python scripts/load_layers.py --layer ssurgo --source-file ..\data\processed\ssurgo_soils.gpkg --layer-name ssurgo_soils
python scripts/verify_layers.py
```
