# Sri Lanka Market Price Intelligence

Streamlit dashboard for importing, exploring, comparing, and exporting daily food commodity prices published by the Central Bank of Sri Lanka (CBSL).

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

The SQLite database is created at `data/prices.db` the first time the application runs. Use **Fetch a report** for a single date or **Historical backfill** to import a date range. The backfill skips dates without published reports and can be rerun to resume; existing dates are skipped. CBSL has used two PDF filename suffix formats, and the importer tries both.

## Streamlit Community Cloud

1. Push this project to a private GitHub repository.
2. In Streamlit Community Cloud, create an app from that repository and set the entrypoint to `app.py`.
3. In the app's **Sharing** settings, select **Only specific people can view this app** and invite your team by email.
4. Create a Supabase project. In **Connect**, choose the **Session pooler** and copy its URI. Use the URI shown for your project; don't construct the pooler host or username yourself.
5. In the app's **Advanced settings → Secrets**, add the following, replacing the example with the Supabase URI and your database password:

   ```toml
   [database]
   url = "postgresql://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require"
   ```

The app creates its tables automatically in PostgreSQL. Never commit `.streamlit/secrets.toml` or a real database URL. `secrets.toml.example` contains placeholders only. For local development, SQLite remains the default; set `DATABASE_URL` or add the same local secrets file to use PostgreSQL. Community Cloud does not guarantee persistence of files stored on the app machine, so PostgreSQL is required to retain imported prices across restarts. Community Cloud currently allows one private app per account. Supabase recommends the shared Session pooler for clients that need an IPv4 endpoint; its dashboard supplies the project-specific URI and username. URL-encode reserved characters in the password. See [Supabase connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres), [Streamlit's Community Cloud deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy), [sharing and private app settings](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app), [secrets guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), and [data persistence note](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data).

## Dashboard sections

- **Overview:** latest prices, day-over-day changes, and report source.
- **Explore prices:** category, item, market, price type, and history window filters.
- **Compare:** plot up to five items over time.
- **Intelligence:** threshold checks, retail/wholesale spread, and seasonal averages.
- **Export data:** download observations and the report index as CSV.

The parser reads the detailed table on page 2 and uses PDF column positions to preserve blank market cells. A missing price (`n.a.`) remains empty in the database.

## Notes

- Prices retain the units stated in the CBSL report; currencies and units are not converted.
- Threshold checks run when the dashboard is open. Scheduled notifications are not configured.
- Historical backfill makes one or two requests per calendar day, depending on the archive filename format. Start with the default 1 January 2016 range and allow the import to finish; weekends and holidays have no report.
