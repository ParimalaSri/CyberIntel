import duckdb

path = r"D:\Cyber_DataSet\2026-09-14T10-00-00.json"

con = duckdb.connect()

df = con.sql(f"""
    SELECT *
    FROM read_json_auto('{path}')
    LIMIT 5000
""").df()

print(df.shape)
print(df.head())