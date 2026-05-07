import sqlite3
import pandas as pd
from loguru import logger
from time import strptime
from datetime import datetime

RIM_SCRIPT_PATH = "DWH_RIM.txt"
SDM_PATH = "../SDM/GO_SDM.db"
DWH_PATH = "GO_DWH.db"

def is_sqlite(log_record):
    return log_record["extra"].get("sqlite") is True

logger.remove()
logger.add("DWH.log", level="DEBUG", filter=lambda r: not is_sqlite(r))
logger.add("DWH_SQlite.log", level="INFO", filter=is_sqlite, rotation="10 MB")

sqliteLogger = logger.bind(sqlite= True)

conn_SDM = sqlite3.connect(SDM_PATH)
conn_DWH = sqlite3.connect(DWH_PATH)
conn_DWH.set_trace_callback(sqliteLogger.info)
conn_DWH.execute("PRAGMA foreign_keys = 1")

def create_dwh_db():
    conn_DWH.set_trace_callback(None)

    with open(RIM_SCRIPT_PATH, "r") as f:
        sql = f.read()

    try:
        conn_DWH.executescript(sql)
        conn_DWH.commit()
        logger.info("DWH database aangemaakt op basis van RIM script")
    except Exception as e:
        logger.error(f"fout bij aanmaken DWH database: {e}")
        raise


    conn_DWH.set_trace_callback(sqliteLogger.info)

# Als er geen tabellen bestaan in de DB, run dan het DWH script
if conn_DWH.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0:
    create_dwh_db()

# COUNTRY - Not directly in DWH
# Cached class
class country:
    countries = None

    def get(self):
        # Cached getter that returns a copy
        if self.countries is None:
            self._create()
        return self.countries.copy()

    def clear(self):
        self.countries = None

    def _create(self):
        # Get SDM Values
        SDM_crm_country = pd.read_sql_query("SELECT * FROM Crm_Country", conn_SDM)
        SDM_sales_country = pd.read_sql_query("SELECT * FROM country", conn_SDM)
        SDM_sales_territory = pd.read_sql_query("SELECT * FROM Sales_Territory", conn_SDM)

        # JOIN sales_territory + crm_country
        SDM_crm_country = ((SDM_crm_country.merge(SDM_sales_territory, how="left")
                           .drop(columns={'SALES_TERRITORY_CODE'}))
                           .rename(columns={"COUNTRY_EN":"COUNTRY"}))
        # Multiload crm_country + sales_country
        self.countries = SDM_crm_country.merge(SDM_sales_country, how="outer")

def countries():
    # Get SDM Values
    SDM_crm_country = pd.read_sql_query("SELECT * FROM Crm_Country", conn_SDM)
    SDM_sales_country = pd.read_sql_query("SELECT * FROM country", conn_SDM)
    SDM_sales_territory = pd.read_sql_query("SELECT * FROM Sales_Territory", conn_SDM)

    # JOIN sales_territory + crm_country
    SDM_crm_country = ((SDM_crm_country.merge(SDM_sales_territory, how="left")
                       .drop(columns={'SALES_TERRITORY_CODE'}))
                       .rename(columns={"COUNTRY_EN":"COUNTRY"}))
    # Multiload crm_country + sales_country
    return SDM_crm_country.merge(SDM_sales_country, how="outer")

# Customer Site - Type 2
# Merge HQ + Country
SDM_Customer_Headquarters = pd.read_sql_query("SELECT * FROM Customer_Headquarters", conn_SDM)
SDM_Customer_Headquarters = SDM_Customer_Headquarters.merge(countries(), how="left").drop(columns={"COUNTRY_CODE"})
SDM_Customer_Headquarters.columns = "HQ_" + SDM_Customer_Headquarters.columns.values
SDM_Customer_Headquarters = SDM_Customer_Headquarters.rename(columns={"HQ_CUSTOMER_CODEMR": "CUSTOMER_CODEMR", "HQ_SEGMENT_CODE": "SEGMENT_CODE"})

# Merge HQ + Customer Segment
SDM_Customer_Segment = pd.read_sql_query("SELECT * FROM Customer_Segment", conn_SDM)
SDM_Customer_Headquarters = SDM_Customer_Headquarters.merge(SDM_Customer_Segment, how="left").drop(columns={"SEGMENT_CODE"})

# Merge Age Group + Sales Demographic
SDM_Age_Group = pd.read_sql_query("SELECT * FROM Age_Group", conn_SDM)
SDM_Sales_Demographic = pd.read_sql_query("SELECT * FROM Sales_Demographic", conn_SDM)
SDM_Sales_Demographic = SDM_Sales_Demographic.merge(SDM_Age_Group, how="left").drop(columns={"AGE_GROUP_CODE"})

# Transform SALES_PERCENT to age brackets
for lower_age in SDM_Sales_Demographic["LOWER_AGE"].unique():
    for upper_age in SDM_Sales_Demographic.loc[SDM_Sales_Demographic["LOWER_AGE"] == lower_age, "UPPER_AGE"].unique():
        SDM_Sales_Demographic.loc[(SDM_Sales_Demographic["LOWER_AGE"] == lower_age) & (SDM_Sales_Demographic["UPPER_AGE"] == upper_age), f"AGE{lower_age}_{upper_age}"] = SDM_Sales_Demographic["SALES_PERCENT"]

# Fold age brackets in to a single demographics row
SDM_Sales_Demographic = SDM_Sales_Demographic.drop(columns={"DEMOGRAPHIC_CODE", "SALES_PERCENT", "UPPER_AGE", "LOWER_AGE"})
SDM_Sales_Demographic = SDM_Sales_Demographic.set_index("CUSTOMER_CODEMR").groupby(level=0).transform(lambda x: sorted(x, key=lambda k: pd.isna(k))).dropna(how="all")

# Merge HQ + Sales Demographic
SDM_Customer_Headquarters = SDM_Customer_Headquarters.merge(SDM_Sales_Demographic, how="left", on="CUSTOMER_CODEMR")

# Merge Customer Type + Customer
SDM_Customer_Type = pd.read_sql_query("SELECT * FROM Customer_Type", conn_SDM)
SDM_Customer = pd.read_sql_query("SELECT * FROM Customer", conn_SDM)

# Merge HQ + Customer
SDM_Customer = SDM_Customer.merge(SDM_Customer_Type, how="left").drop(columns={"CUSTOMER_TYPE_CODE"})
SDM_Customer = SDM_Customer.merge(SDM_Customer_Headquarters, how="outer")
SDM_Customer["COMPANY_NAME"] = SDM_Customer["COMPANY_NAME"].fillna(SDM_Customer["HQ_CUSTOMER_NAME"])
SDM_Customer = SDM_Customer.drop(columns={"HQ_CUSTOMER_NAME", "CUSTOMER_CODEMR"})

# Get Customer Store + Retailer Site
SDM_Customer_Store = pd.read_sql_query("SELECT CUSTOMER_CODE, STREET as ADDRESS1, ADDITION as ADDRESS2, CITY, STATE as REGION, ZIPCODE as POSTAL_ZONE, COUNTRY_CODE, ACTIVE_INDICATOR, CUSTOMER_SITE_CODE as PK_CUSTOMER_STORE FROM Customer_Store", conn_SDM)
SDM_Retailer_Site = pd.read_sql_query("SELECT RETAILER_CODE as CUSTOMER_CODE, ADDRESS1, ADDRESS2, CITY, REGION, POSTAL_ZONE, COUNTRY_CODE, ACTIVE_INDICATOR, RETAILER_SITE_CODE as PK_RETAILER_SITE FROM retailer_site", conn_SDM)

# Multiload Customer Store + Retailer Site, merge with country, merge with customer
SDM_Customer_Site = SDM_Customer_Store.merge(SDM_Retailer_Site, how="outer")
SDM_Customer_Site = SDM_Customer_Site.merge(countries(), how="left").drop(columns={"COUNTRY_CODE"})
SDM_Customer_Site = SDM_Customer_Site.merge(SDM_Customer, how="outer").drop(columns={"CUSTOMER_CODE"})

# Get and prepare DWH values
DWH_Customer_Site = pd.read_sql_query("SELECT * FROM customer_site WHERE VALID_TILL IS NULL", conn_DWH)
DWH_Customer_Site = DWH_Customer_Site.drop(columns={"VALID_FROM", "VALID_TILL"})
DWH_Customer_Site = DWH_Customer_Site.astype({"ACTIVE_INDICATOR": "float64", "PK_CUSTOMER_STORE": "float64", "PK_RETAILER_SITE": "float64"})

# Determine difference between SDM and DWH
diff = SDM_Customer_Site.merge(DWH_Customer_Site, indicator=True, how="outer")
new = diff.loc[(diff["_merge"] == "left_only")].drop("_merge", axis=1)
outdated = diff.loc[(diff["_merge"] == "right_only"), ["CUSTOMER_STORE_SK", "PK_CUSTOMER_STORE", "PK_RETAILER_SITE"]]

# Determine if values are new or updated
new = (new.drop(['CUSTOMER_STORE_SK'], axis=1)
       .merge(outdated.loc[outdated['PK_CUSTOMER_STORE'].notna(), ["CUSTOMER_STORE_SK", "PK_CUSTOMER_STORE"]], left_on="PK_CUSTOMER_STORE", right_on="PK_CUSTOMER_STORE", how="left")
       .merge(outdated.loc[outdated['PK_RETAILER_SITE'].notna(), ["CUSTOMER_STORE_SK", "PK_RETAILER_SITE"]], left_on="PK_RETAILER_SITE", right_on="PK_RETAILER_SITE", how="left"))
new["already_exists"] = new["CUSTOMER_STORE_SK_x"].notna() | new["CUSTOMER_STORE_SK_y"].notna()

# Update DWH
cur = conn_DWH.cursor()
try:
    # Set valid till date for outdated DWH values
    for record in outdated.to_dict(orient="records"):
        try:
            cur.execute(f"UPDATE customer_site SET valid_till = \'{datetime.now().strftime('%d-%b-%Y %H:%M:%S %p')}\' WHERE CUSTOMER_STORE_SK = :CUSTOMER_STORE_SK", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij zetten van einddatum in database: {database} PK:{key}", database="customer_site", key=record["CUSTOMER_STORE_SK"])

    # Insert new values in to DWH
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO customer_site(ADDRESS1, ADDRESS2, CITY, REGION, POSTAL_ZONE, TERRITORY_NAME_EN, FLAG_IMAGE, CURRENCY_NAME, LANGUAGE, COUNTRY, COMPANY_NAME, CUSTOMER_TYPE_EN, SEGMENT_DESCRIPTION, SEGMENT_NAME, HQ_ADDRESS1, HQ_ADDRESS2, HQ_CITY, HQ_REGION, HQ_POSTAL_ZONE, HQ_PHONE, HQ_FAX, HQ_LANGUAGE, HQ_CURRENCY_NAME, HQ_FLAG_IMAGE, HQ_TERRITORY_NAME_EN, HQ_COUNTRY, AGE61_70, AGE51_60, AGE41_50, AGE31_40, AGE21_30, AGE0_20, ACTIVE_INDICATOR, PK_CUSTOMER_STORE, PK_RETAILER_SITE, VALID_FROM) VALUES(:ADDRESS1, :ADDRESS2, :CITY, :REGION, :POSTAL_ZONE, :TERRITORY_NAME_EN, :FLAG_IMAGE, :CURRENCY_NAME, :LANGUAGE, :COUNTRY, :COMPANY_NAME, :CUSTOMER_TYPE_EN, :SEGMENT_DESCRIPTION, :SEGMENT_NAME, :HQ_ADDRESS1, :HQ_ADDRESS2, :HQ_CITY, :HQ_REGION, :HQ_POSTAL_ZONE, :HQ_PHONE, :HQ_FAX, :HQ_LANGUAGE, :HQ_CURRENCY_NAME, :HQ_FLAG_IMAGE, :HQ_TERRITORY_NAME_EN, :HQ_COUNTRY, :AGE61_70, :AGE51_60, :AGE41_50, :AGE31_40, :AGE21_30, :AGE0_20, :ACTIVE_INDICATOR, :PK_CUSTOMER_STORE, :PK_RETAILER_SITE, (CASE WHEN :already_exists THEN \'{datetime.now().strftime('%d-%b-%Y %H:%M:%S %p')}\' ELSE \'{datetime.fromtimestamp(0).strftime('%d-%b-%Y %H:%M:%S %p')}\' END))", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="customer_site", key=f'{record["PK_CUSTOMER_STORE"]}-{record["PK_RETAILER_SITE"]}')

finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {outdated} rijen voorzien van einddatum", tabel="customer_site", new=len(new.index), outdated=len(outdated.index))


# Customer Contact - Type 2
# Get SDM values
SDM_Customer_Contact = pd.read_sql_query("SELECT * FROM Customer_Contact", conn_SDM)
SDM_Customer_Contact = SDM_Customer_Contact.astype({"EXTENSION": "float64"})

# Determine name
SDM_Customer_Contact["NAME"] = SDM_Customer_Contact["FIRST_NAME"] + " " + SDM_Customer_Contact["LAST_NAME"]

# Get current DWH SK-PK connections and apply
DWH_Customer_Site = pd.read_sql_query("SELECT CUSTOMER_STORE_SK, PK_RETAILER_SITE FROM customer_site WHERE VALID_TILL IS NULL", conn_DWH)
SDM_Customer_Contact = SDM_Customer_Contact.merge(DWH_Customer_Site, how="left", left_on="CUSTOMER_SITE_CODE", right_on="PK_RETAILER_SITE").drop(columns={"CUSTOMER_SITE_CODE", "PK_RETAILER_SITE"})

# Get and prepare DWH values
DWH_Customer_Contact = pd.read_sql_query("SELECT * FROM customer_contact WHERE VALID_TILL IS NULL", conn_DWH)
DWH_Customer_Contact = DWH_Customer_Contact.drop(columns={"VALID_FROM", "VALID_TILL"})

# Determine difference between SDM and DWH
diff = SDM_Customer_Contact.merge(DWH_Customer_Contact, indicator=True, how="outer")
new = diff.loc[(diff["_merge"] == "left_only")].drop("_merge", axis=1)
outdated = diff.loc[(diff["_merge"] == "right_only"), ["CUSTOMER_CONTACT_SK", "CUSTOMER_CONTACT_CODE"]]

# Determine if values are new or updated
new = new.drop(['CUSTOMER_CONTACT_SK'], axis=1).merge(outdated, how="left")
new["already_exists"] = new["CUSTOMER_CONTACT_SK"].notna()

# Update DWH
cur = conn_DWH.cursor()
try:
    # Set valid till date for outdated DWH values
    for record in outdated.to_dict(orient="records"):
        try:
            cur.execute(f"UPDATE customer_contact SET valid_till = \'{datetime.now().strftime('%d-%b-%Y %H:%M:%S %p')}\' WHERE CUSTOMER_CONTACT_SK = :CUSTOMER_CONTACT_SK", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij zetten van einddatum in database: {database} PK:{key}", database="customer_contact", key=record["CUSTOMER_CONTACT_SK"])

    # Insert new values in to DWH
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO customer_contact(CUSTOMER_STORE_SK, NAME, JOB_POSITION_EN, EXTENSION, GENDER, CUSTOMER_CONTACT_CODE, VALID_FROM) VALUES (:CUSTOMER_STORE_SK, :NAME, :JOB_POSITION_EN, :EXTENSION, :GENDER, :CUSTOMER_CONTACT_CODE, (CASE WHEN :already_exists THEN \'{datetime.now().strftime('%d-%b-%Y %H:%M:%S %p')}\' ELSE \'{datetime.fromtimestamp(0).strftime('%d-%b-%Y %H:%M:%S %p')}\' END))", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="customer_contact", key=record["CUSTOMER_CONTACT_CODE"])

finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {outdated} rijen voorzien van einddatum", tabel="customer_contact", new=len(new.index), outdated=len(outdated.index))


# Order Method - Type 1
# Get SDM values
SDM_Order_Method = pd.read_sql_query("SELECT * FROM order_method", conn_SDM)

# Get DWH values
DWH_Order_Method = pd.read_sql_query("SELECT * FROM order_method", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Order_Method.merge(DWH_Order_Method, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM order_method WHERE ORDER_METHOD_CODE = :ORDER_METHOD_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="order_method", key=record["ORDER_METHOD_CODE"])

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO order_method VALUES (:ORDER_METHOD_CODE, :ORDER_METHOD_EN)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="order_method", key=record["ORDER_METHOD_CODE"])
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="order_method", new=len(new.index), removed=len(removed.index))

# Return Reason - Type 1
# Get SDM values
SDM_Return_Reason = pd.read_sql_query("SELECT * FROM return_reason", conn_SDM)

# Get DWH values
DWH_Return_Reason= pd.read_sql_query("SELECT * FROM return_reason", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Return_Reason.merge(DWH_Return_Reason, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM return_reason WHERE RETURN_REASON_CODE = :RETURN_REASON_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="return_reason", key=record["RETURN_REASON_CODE"])

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO return_reason VALUES (:RETURN_REASON_CODE, :RETURN_DESCRIPTION_EN)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="return_reason", key=record["RETURN_REASON_CODE"])
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="return_reason", new=len(new.index), removed=len(removed.index))

# Course - Type 1
# Get SDM values
SDM_Course = pd.read_sql_query("SELECT * FROM course", conn_SDM)

# Get DWH values
DWH_Course = pd.read_sql_query("SELECT * FROM course", conn_DWH)
DWH_Course = DWH_Course.astype({"COURSE_CODE": "int64"})

# Determine difference between SDM and DWH
diff = SDM_Course.merge(DWH_Course, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM course WHERE COURSE_CODE = :COURSE_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="course", key=record["COURSE_CODE"])

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO course VALUES (:COURSE_CODE, :COURSE_DESCRIPTION)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="course", key=record["COURSE_CODE"])
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="course", new=len(new.index), removed=len(removed.index))

# Satisfaction Type - Type 1
# Get SDM values
SDM_Satisfaction_Type = pd.read_sql_query("SELECT * FROM satisfaction_type", conn_SDM)

# Get DWH values
DWH_Satisfaction_Type= pd.read_sql_query("SELECT * FROM satisfaction_type", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Satisfaction_Type.merge(DWH_Satisfaction_Type, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM satisfaction_type WHERE SATISFACTION_TYPE_CODE = :SATISFACTION_TYPE_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="satisfaction_type", key=record["SATISFACTION_TYPE_CODE"])

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO satisfaction_type VALUES (:SATISFACTION_TYPE_CODE, :SATISFACTION_TYPE_DESCRIPTION)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="satisfaction_type", key=record["SATISFACTION_TYPE_CODE"])
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="satisfaction_type", new=len(new.index), removed=len(removed.index))

# Sales Branch - Type 2
# Get and prepare SDM values
SDM_Sales_Branch = pd.read_sql_query("SELECT * FROM sales_branch", conn_SDM)
SDM_Sales_Branch = SDM_Sales_Branch.rename(columns = {
    "SALES_BRANCH_CODE": "PK_SALES_BRANCH"
})

SDM_Sales_Office = pd.read_sql_query("SELECT * FROM sales_office", conn_SDM)
SDM_Sales_Office = SDM_Sales_Office.rename(columns = {
    "SALES_OFFICE_CODE": "PK_SALES_OFFICE",
    "STREET": "ADDRESS1",
    "ADDITION": "ADDRESS2",
    "ZIPCODE": "POSTAL_ZONE"
})

# Cast PK's to Int64 to prevent decimal values
SDM_Sales_Branch["PK_SALES_BRANCH"] = pd.to_numeric(SDM_Sales_Branch["PK_SALES_BRANCH"], errors = "coerce").astype("Int64")
SDM_Sales_Office["PK_SALES_OFFICE"] = pd.to_numeric(SDM_Sales_Office["PK_SALES_OFFICE"], errors = "coerce").astype("Int64")

# Merge sales_branch with sales_office, merge countries with the dataframe
DF_Branch_Office = SDM_Sales_Branch.merge(SDM_Sales_Office, how = "outer")
DF_Branch_Office = DF_Branch_Office.merge(countries(), how = "left").drop(columns = {"COUNTRY_CODE"})

# Get and prepare DWH values
DWH_Sales_Branch = pd.read_sql_query("SELECT * FROM sales_branch WHERE VALID_TILL IS NULL", conn_DWH)
DWH_Sales_Branch["PK_SALES_BRANCH"] = pd.to_numeric(DWH_Sales_Branch["PK_SALES_BRANCH"], errors="coerce").astype("Int64")
DWH_Sales_Branch["PK_SALES_OFFICE"] = pd.to_numeric(DWH_Sales_Branch["PK_SALES_OFFICE"], errors="coerce").astype("Int64")

# Determine difference between SDM and DWH
diff = DF_Branch_Office.merge(DWH_Sales_Branch, indicator = True, how = "outer")
new = diff.loc[(diff["_merge"] == "left_only")].drop("_merge", axis = 1)
outdated = diff.loc[(diff["_merge"] == "right_only"), ["SALES_BRANCH_SK", "PK_SALES_BRANCH", "PK_SALES_OFFICE"]]

# Determine if values are new or updated
new = (new.drop(["SALES_BRANCH_SK"], axis = 1)
       .merge(outdated.loc[outdated["PK_SALES_BRANCH"].notna(), ["SALES_BRANCH_SK", "PK_SALES_BRANCH"]], left_on = "PK_SALES_BRANCH", right_on = "PK_SALES_BRANCH",  how = "left")
       .merge(outdated.loc[outdated["PK_SALES_OFFICE"].notna(), ["SALES_BRANCH_SK", "PK_SALES_OFFICE"]], left_on = "PK_SALES_OFFICE", right_on = "PK_SALES_OFFICE",  how = "left"))
new["already_exists"] = new["SALES_BRANCH_SK_x"].notna() | new["SALES_BRANCH_SK_y"].notna()

# Update DWH
cur = conn_DWH.cursor()

try:
    # Set valid till date for outdated DWH values
    for record in outdated.to_dict(orient = "records"):
        try:
            cur.execute(f"UPDATE sales_branch SET valid_till = \'{datetime.now().strftime('%d-%b-%Y %H:%M:%S %p')}\' WHERE SALES_BRANCH_SK = :SALES_BRANCH_SK", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij zetten van einddatum in database: {database} PK: {key}", database = "sales_branch", key = record["SALES_BRANCH_SK"])

    # Insert new values in to DWH
    for record in new.to_dict(orient = "records"):
        try:
            cur.execute(f"INSERT INTO sales_branch(ADDRESS1, ADDRESS2, CITY, REGION, POSTAL_ZONE, TERRITORY_NAME_EN, FLAG_IMAGE, CURRENCY_NAME, LANGUAGE, COUNTRY, PK_SALES_BRANCH, PK_SALES_OFFICE, VALID_FROM) VALUES (:ADDRESS1, :ADDRESS2, :CITY, :REGION, :POSTAL_ZONE, :TERRITORY_NAME_EN, :FLAG_IMAGE, :CURRENCY_NAME, :LANGUAGE, :COUNTRY, :PK_SALES_BRANCH, :PK_SALES_OFFICE, (CASE WHEN :already_exists THEN \'{datetime.now().strftime('%d-%b-%Y %H:%M:%S %p')}\' ELSE \'{datetime.fromtimestamp(0).strftime('%d-%b-%Y %H:%M:%S %p')}\' END))", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK: {key}", database = "sales_branch", key =f'{record["PK_SALES_BRANCH"]}-{record["PK_SALES_OFFICE"]}')

finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {outdated} rijen voorzien van einddatum", tabel = "sales_branch", new = len(new.index), outdated = len(outdated.index))

# Sales Representative - Type 2
# Get and prepare SDM values and connect sales branch SK
SDM_Sales_Staff = pd.read_sql_query("SELECT * FROM sales_staff", conn_SDM)
SDM_Sales_Staff = SDM_Sales_Staff.rename(columns = {"SALES_STAFF_CODE": "PK_SALES_STAFF"})
SK_Sales_Branch = pd.read_sql_query("SELECT SALES_BRANCH_SK as SALES_OFFICE_CODE, PK_SALES_BRANCH FROM sales_branch WHERE VALID_TILL IS NULL", conn_DWH)
SK_Sales_Branch = SK_Sales_Branch.astype({"PK_SALES_BRANCH": "float64"})
SDM_Sales_Staff = SDM_Sales_Staff.merge(SK_Sales_Branch, left_on="SALES_BRANCH_CODE", right_on="PK_SALES_BRANCH").drop(columns={"PK_SALES_BRANCH", "SALES_BRANCH_CODE"})

SDM_Sales_Representative = pd.read_sql_query("SELECT * FROM sales_representative", conn_SDM)
SDM_Sales_Representative = SDM_Sales_Representative.rename(columns = {"SALES_REPRESENTATIVE_CODE": "PK_SALES_REPRESENTATIVE", "SALES_OFFICE_CODE": "SALES_OFFICE_PK"})
SK_Sales_Office = pd.read_sql_query("SELECT SALES_BRANCH_SK as SALES_OFFICE_CODE, PK_SALES_OFFICE FROM sales_branch WHERE VALID_TILL IS NULL", conn_DWH)
SK_Sales_Office = SK_Sales_Office.astype({"PK_SALES_OFFICE": "float64"})
SDM_Sales_Representative = SDM_Sales_Representative.merge(SK_Sales_Office, left_on="SALES_OFFICE_PK", right_on="PK_SALES_OFFICE").drop(columns={"PK_SALES_OFFICE", "SALES_OFFICE_PK"})

# Merge sales_staff with sales_representative
DF_Staff_Representative = SDM_Sales_Staff.merge(SDM_Sales_Representative, how = "outer")

# Get and prepare DWH values
DWH_Sales_Representative = pd.read_sql_query("SELECT * FROM sales_representative WHERE VALID_TILL IS NULL", conn_DWH)
DWH_Sales_Representative = DWH_Sales_Representative.astype({"PK_SALES_STAFF": "float64", "SALES_OFFICE_CODE": "float64", "PK_SALES_REPRESENTATIVE": "float64", "MANAGER_CODE": "float64"})

# We're not currently connecting the manager code
# Drop it to prevent endless reloads
DF_Staff_Representative = DF_Staff_Representative.drop(columns={"MANAGER_CODE"})

# Determine difference between SDM and DWH
diff = DF_Staff_Representative.merge(DWH_Sales_Representative, indicator = True, how = "outer")
new = diff.loc[(diff["_merge"] == "left_only")].drop("_merge", axis = 1)
outdated = diff.loc[diff["_merge"] == "right_only", ["SALES_REPRESENTATIVE_SK", "PK_SALES_STAFF", "PK_SALES_REPRESENTATIVE"]]

# Determine if values are new or updated
new = (new.drop(["SALES_REPRESENTATIVE_SK"], axis = 1)
       .merge(outdated.loc[outdated["PK_SALES_STAFF"].notna(), ["SALES_REPRESENTATIVE_SK", "PK_SALES_STAFF"]], left_on = "PK_SALES_STAFF", right_on = "PK_SALES_STAFF",  how = "left")
       .merge(outdated.loc[outdated["PK_SALES_REPRESENTATIVE"].notna(), ["SALES_REPRESENTATIVE_SK", "PK_SALES_REPRESENTATIVE"]], left_on = "PK_SALES_REPRESENTATIVE", right_on = "PK_SALES_REPRESENTATIVE",  how = "left"))
new["already_exists"] = new["SALES_REPRESENTATIVE_SK_x"].notna() | new["SALES_REPRESENTATIVE_SK_y"].notna()

# Update DWH
cur = conn_DWH.cursor()

try:
    # Set valid till date for outdated DWH values
    for record in outdated.to_dict(orient = "records"):
        try:
            cur.execute(f"UPDATE sales_representative SET valid_till = \'{datetime.now().strftime('%d-%b-%Y %H:%M:%S %p')}\' WHERE SALES_REPRESENTATIVE_SK = :SALES_REPRESENTATIVE_SK", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij zetten van einddatum in database: {database} PK: {key}", database = "sales_representative", key = record["SALES_REPRESENTATIVE_SK"])

    # Insert new values in to DWH
    for record in new.to_dict(orient = "records"):
        try:
            cur.execute(f"INSERT INTO sales_representative(FIRST_NAME, LAST_NAME, POSITION_EN, WORK_PHONE, EXTENSION, FAX, EMAIL, DATE_HIRED, PK_SALES_STAFF, PK_SALES_REPRESENTATIVE, SALES_OFFICE_CODE, VALID_FROM) VALUES (:FIRST_NAME, :LAST_NAME, :POSITION_EN, :WORK_PHONE, :EXTENSION, :FAX, :EMAIL, :DATE_HIRED, :PK_SALES_STAFF, :PK_SALES_REPRESENTATIVE, :SALES_OFFICE_CODE,  (CASE WHEN :already_exists THEN \'{datetime.now().strftime('%d-%b-%Y %H:%M:%S %p')}\' ELSE \'{datetime.fromtimestamp(0).strftime('%d-%b-%Y %H:%M:%S %p')}\' END))", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK: {key}", database = "sales_representative", key = f'{record["PK_SALES_REPRESENTATIVE"]}-{record["PK_SALES_STAFF"]}')

finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {outdated} rijen voorzien van einddatum", tabel = "sales_representative", new = len(new.index), outdated = len(outdated.index))
# TODO MANAGER CODE

# Product - Type 1
# Get SDM values
SDM_Product = pd.read_sql_query("SELECT * FROM product", conn_SDM)
SDM_Product_Type = pd.read_sql_query("SELECT * FROM product_type", conn_SDM)
SDM_Product_Line = pd.read_sql_query("SELECT * FROM product_line", conn_SDM)

# Merge SDM values and drop unnecessary columns
SDM_Product_Type = SDM_Product_Type.merge(SDM_Product_Line, how="left").drop(columns={"PRODUCT_LINE_CODE"})
SDM_Product = SDM_Product.merge(SDM_Product_Type, how="left").drop(columns={"PRODUCT_TYPE_CODE", "PRODUCTION_COST", "MARGIN"})

# Get DWH values
DWH_Product = pd.read_sql_query("SELECT * FROM product", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Product.merge(DWH_Product, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM product WHERE PRODUCT_NUMBER = :PRODUCT_NUMBER", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="product", key=record["PRODUCT_NUMBER"])

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO product VALUES (:PRODUCT_NUMBER, :INTRODUCTION_DATE, :PRODUCT_NAME, :PRODUCT_TYPE_EN, :PRODUCT_LINE_EN, :PRODUCT_IMAGE, :LANGUAGE, :DESCRIPTION)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="product_forecast", key=record["PRODUCT_NUMBER"])
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="product", new=len(new.index), removed=len(removed.index))



def toDay(datestring):
    return strptime(datestring, "%d-%b-%Y %H:%M:%S %p").tm_mday
def toMonth(datestring):
    return strptime(datestring, "%d-%b-%Y %H:%M:%S %p").tm_mon
def toYear(datestring):
    return strptime(datestring, "%d-%b-%Y %H:%M:%S %p").tm_year
def toQuarter(month):
    if month <= 0 | month > 12:
        return 0
    return int((month - 1) / 3 + 1)

# Date - Type 1
# Get SDM dates
SDM_Dates = pd.read_sql_query("SELECT ORDER_DATE FROM Order_Header UNION SELECT RETURN_DATE FROM returned_item", conn_SDM)

# Transform dates
SDM_Dates["DAY"] = SDM_Dates["ORDER_DATE"].apply(toDay)
SDM_Dates["MONTH"] = SDM_Dates["ORDER_DATE"].apply(toMonth)
SDM_Dates["YEAR"] = SDM_Dates["ORDER_DATE"].apply(toYear)
SDM_Dates = SDM_Dates.drop(columns={"ORDER_DATE"}).drop_duplicates()

# Get SDM months
SDM_Months = pd.read_sql_query("SELECT SALES_PERIOD AS MONTH, SALES_YEAR AS YEAR FROM Sales_Target UNION SELECT MONTH, YEAR FROM Product_Forecast UNION SELECT INVENTORY_MONTH AS MONTH, INVENTORY_YEAR AS YEAR FROM Inventory_Levels", conn_SDM)

# Combine with dates and transform
SDM_Months = SDM_Months.merge(SDM_Dates.drop(columns={"DAY"}), how="outer").drop_duplicates()
SDM_Months["QUARTER"] = SDM_Months["MONTH"].apply(toQuarter)


# Get SDM years
SDM_Years = pd.read_sql_query("SELECT YEAR FROM satisfaction UNION SELECT YEAR FROM training", conn_SDM)

# Combine with months
SDM_Years = pd.concat([SDM_Years, SDM_Months.drop(columns={"MONTH", "QUARTER"})]).drop_duplicates()

# Get DWH values
DWH_Dates = pd.read_sql_query("SELECT * FROM date", conn_DWH)
DWH_Months = pd.read_sql_query("SELECT * FROM month_year", conn_DWH)
DWH_Years = pd.read_sql_query("SELECT * FROM year", conn_DWH)

# Update year in DWH
# Determine difference between SDM and DWH
diff = SDM_Years.merge(DWH_Years, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM year WHERE YEAR = :YEAR", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="year", key=record["YEAR"])

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO year VALUES (:YEAR)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="year", key=record["YEAR"])
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="year", new=len(new.index), removed=len(removed.index))

# Update months in DWH
# Determine difference between SDM and DWH
diff = SDM_Months.merge(DWH_Months, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM month_year WHERE YEAR = :YEAR AND MONTH = :MONTH", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="month_year", key=f'{record["YEAR"]}-{record["MONTH"]}')

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO month_year VALUES (:QUARTER, :MONTH, :YEAR)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="month_year", key=f'{record["YEAR"]}-{record["MONTH"]}')
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="month_year", new=len(new.index), removed=len(removed.index))

# Update dates in DWH
# Determine difference between SDM and DWH
diff = SDM_Dates.merge(DWH_Dates, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM date WHERE YEAR = :YEAR AND MONTH = :MONTH AND DAY = :DAY", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="date", key=f'{record["YEAR"]}-{record["MONTH"]}-{record["DAY"]}')

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO date VALUES (:DAY, :MONTH, :YEAR)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="date", key=f'{record["YEAR"]}-{record["MONTH"]}-{record["DAY"]}')
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="date", new=len(new.index), removed=len(removed.index))

# Order - Type 1
# Get SDM values, merge product measurables
SDM_Orders = pd.read_sql_query("SELECT * FROM order_details d LEFT JOIN Order_Header h ON d.ORDER_NUMBER = h.ORDER_NUMBER", conn_SDM)
SDM_Product = pd.read_sql_query("SELECT PRODUCT_NUMBER, MARGIN, PRODUCTION_COST FROM product", conn_SDM)
SDM_Orders = SDM_Orders.merge(SDM_Product).drop(columns={"ORDER_NUMBER", "RETAILER_NAME"}).rename(columns={"ORDER_DETAIL_CODE": "ORDER_CODE"})

# Get day/month/year values - transform date for comparison
SDM_Orders["ORDER_DAY"] = SDM_Orders["ORDER_DATE"].apply(toDay)
SDM_Orders["ORDER_MONTH"] = SDM_Orders["ORDER_DATE"].apply(toMonth)
SDM_Orders["ORDER_YEAR"] = SDM_Orders["ORDER_DATE"].apply(toYear)
SDM_Orders["ORDER_DATE"] = pd.to_datetime(SDM_Orders["ORDER_DATE"], format="%d-%b-%Y %H:%M:%S %p")

# Obtain and set SK's
# Sales_Branch
SK_Sales_Branch = pd.read_sql_query("SELECT SALES_BRANCH_SK, PK_SALES_BRANCH, VALID_FROM, VALID_TILL FROM sales_branch", conn_DWH)
SK_Sales_Branch["VALID_FROM"] = pd.to_datetime(SK_Sales_Branch["VALID_FROM"], format="%d-%b-%Y %H:%M:%S %p")
SK_Sales_Branch["VALID_TILL"] = pd.to_datetime(SK_Sales_Branch["VALID_TILL"], format="%d-%b-%Y %H:%M:%S %p")
SK_Sales_Branch = SK_Sales_Branch.astype({"PK_SALES_BRANCH": "float64"})

SDM_Orders = SDM_Orders.merge(SK_Sales_Branch, left_on="SALES_BRANCH_CODE", right_on="PK_SALES_BRANCH", how="outer").drop(columns={"SALES_BRANCH_CODE", "PK_SALES_BRANCH"})
SDM_Orders = SDM_Orders.loc[((SDM_Orders['ORDER_DATE'] > SDM_Orders['VALID_FROM']) & (SDM_Orders['VALID_TILL'].isna() | (SDM_Orders['ORDER_DATE'] < SDM_Orders['VALID_TILL'])))].drop(columns={"VALID_FROM", "VALID_TILL"})

# Customer_Site
SK_Customer_Site = pd.read_sql_query("SELECT CUSTOMER_STORE_SK, PK_RETAILER_SITE, VALID_FROM, VALID_TILL FROM customer_site", conn_DWH)
SK_Customer_Site["VALID_FROM"] = pd.to_datetime(SK_Customer_Site["VALID_FROM"], format="%d-%b-%Y %H:%M:%S %p")
SK_Customer_Site["VALID_TILL"] = pd.to_datetime(SK_Customer_Site["VALID_TILL"], format="%d-%b-%Y %H:%M:%S %p")
SK_Customer_Site = SK_Customer_Site.astype({"PK_RETAILER_SITE": "float64"})

SDM_Orders = SDM_Orders.merge(SK_Customer_Site, left_on="RETAILER_SITE_CODE", right_on="PK_RETAILER_SITE", how="outer").drop(columns={"RETAILER_SITE_CODE", "PK_RETAILER_SITE"})
SDM_Orders = SDM_Orders.loc[((SDM_Orders['ORDER_DATE'] > SDM_Orders['VALID_FROM']) & (SDM_Orders['VALID_TILL'].isna() | (SDM_Orders['ORDER_DATE'] < SDM_Orders['VALID_TILL'])))].drop(columns={"VALID_FROM", "VALID_TILL"})

# Customer_Contact
SK_Customer_Contact = pd.read_sql_query("SELECT CUSTOMER_CONTACT_SK, CUSTOMER_CONTACT_CODE, VALID_FROM, VALID_TILL FROM customer_contact", conn_DWH)
SK_Customer_Contact["VALID_FROM"] = pd.to_datetime(SK_Customer_Contact["VALID_FROM"], format="%d-%b-%Y %H:%M:%S %p")
SK_Customer_Contact["VALID_TILL"] = pd.to_datetime(SK_Customer_Contact["VALID_TILL"], format="%d-%b-%Y %H:%M:%S %p")
SK_Customer_Contact = SK_Customer_Contact.astype({"CUSTOMER_CONTACT_CODE": "float64"})

SDM_Orders = SDM_Orders.merge(SK_Customer_Contact, left_on="RETAILER_CONTACT_CODE", right_on="CUSTOMER_CONTACT_CODE", how="outer").drop(columns={"RETAILER_CONTACT_CODE", "CUSTOMER_CONTACT_CODE"})
SDM_Orders = SDM_Orders.loc[((SDM_Orders['ORDER_DATE'] > SDM_Orders['VALID_FROM']) & (SDM_Orders['VALID_TILL'].isna() | (SDM_Orders['ORDER_DATE'] < SDM_Orders['VALID_TILL'])))].drop(columns={"VALID_FROM", "VALID_TILL"})

# Sales_Representative
SK_Sales_Representative = pd.read_sql_query("SELECT SALES_REPRESENTATIVE_SK, PK_SALES_STAFF, VALID_FROM, VALID_TILL FROM sales_representative", conn_DWH)
SK_Sales_Representative["VALID_FROM"] = pd.to_datetime(SK_Sales_Representative["VALID_FROM"], format="%d-%b-%Y %H:%M:%S %p")
SK_Sales_Representative["VALID_TILL"] = pd.to_datetime(SK_Sales_Representative["VALID_TILL"], format="%d-%b-%Y %H:%M:%S %p")
SK_Sales_Representative = SK_Sales_Representative.astype({"PK_SALES_STAFF": "float64"})

SDM_Orders = SDM_Orders.merge(SK_Sales_Representative, left_on="SALES_STAFF_CODE", right_on="PK_SALES_STAFF", how="outer").drop(columns={"SALES_STAFF_CODE", "PK_SALES_STAFF"})
SDM_Orders = SDM_Orders.loc[((SDM_Orders['ORDER_DATE'] > SDM_Orders['VALID_FROM']) & (SDM_Orders['VALID_TILL'].isna() | (SDM_Orders['ORDER_DATE'] < SDM_Orders['VALID_TILL'])))].drop(columns={"VALID_FROM", "VALID_TILL"})

SDM_Orders["TURNOVER"] = SDM_Orders["QUANTITY"] * SDM_Orders["UNIT_SALE_PRICE"]
SDM_Orders["PROFIT"] = (SDM_Orders["UNIT_SALE_PRICE"] - SDM_Orders["UNIT_COST"]) * SDM_Orders["QUANTITY"]
SDM_Orders["DISCOUNT"] = 1 - (SDM_Orders["UNIT_SALE_PRICE"] / SDM_Orders["UNIT_PRICE"])

SDM_Orders["ORDER_DATE"] = SDM_Orders["ORDER_DATE"].dt.strftime("%d-%b-%Y %H:%M:%S %p")

DWH_Orders = pd.read_sql_query("SELECT * FROM order_details", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Orders.merge(DWH_Orders, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM order_details WHERE ORDER_CODE = :ORDER_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="order_detail", key=record["ORDER_CODE"])

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO order_details VALUES (:ORDER_CODE, :PRODUCT_NUMBER, :CUSTOMER_STORE_SK, :CUSTOMER_CONTACT_SK, :SALES_REPRESENTATIVE_SK, :SALES_BRANCH_SK, :ORDER_DATE, :ORDER_DAY, :ORDER_MONTH, :ORDER_YEAR, :ORDER_METHOD_CODE, :MARGIN, :DISCOUNT, :TURNOVER, :PROFIT, :QUANTITY, :UNIT_COST, :UNIT_SALE_PRICE, :UNIT_PRICE)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="order_detail", key=record["ORDER_CODE"])
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="order_detail", new=len(new.index), removed=len(removed.index))

# Return - Type 1
SDM_Returns = pd.read_sql_query("SELECT * FROM returned_item", conn_SDM)
SDM_Returns = SDM_Returns.merge(SDM_Orders.drop(columns={"ORDER_DATE", "ORDER_DAY", "ORDER_MONTH", "ORDER_YEAR"}), left_on="ORDER_DETAIL_CODE", right_on="ORDER_CODE").drop(columns={"ORDER_DETAIL_CODE", "ORDER_CODE"})

SDM_Returns["RETURN_DAY"] = SDM_Returns["RETURN_DATE"].apply(toDay)
SDM_Returns["RETURN_MONTH"] = SDM_Returns["RETURN_DATE"].apply(toMonth)
SDM_Returns["RETURN_YEAR"] = SDM_Returns["RETURN_DATE"].apply(toYear)

SDM_Returns["TURNOVER"] = SDM_Returns["RETURN_QUANTITY"] * SDM_Returns["UNIT_SALE_PRICE"]
SDM_Returns["PROFIT"] = (SDM_Returns["UNIT_SALE_PRICE"] - SDM_Returns["UNIT_COST"]) * SDM_Returns["RETURN_QUANTITY"]

DWH_Returns = pd.read_sql_query("SELECT * FROM returned_item", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Returns.merge(DWH_Returns, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM returned_item WHERE RETURN_CODE = :RETURN_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="returned_item", key=record["RETURN_CODE"])

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO returned_item VALUES (:RETURN_CODE, :RETURN_DATE, :RETURN_DAY, :RETURN_YEAR, :RETURN_MONTH, :RETURN_REASON_CODE, :RETURN_QUANTITY, :PRODUCT_IMAGE, :QUANTITY, :MARGIN, :DISCOUNT, :PROFIT, :TURNOVER, :UNIT_COST, :UNIT_SALE_PRICE, :UNIT_PRICE, :CUSTOMER_STORE_SK, :SALES_BRANCH_SK, :ORDER_METHOD_CODE, :SALES_REPRESENTATIVE_SK, :CUSTOMER_CONTACT_SK, :PRODUCT_NUMBER)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="returned_item", key=record["RETURN_CODE"])
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="returned_item", new=len(new.index), removed=len(removed.index))

# Training - Type 1
# Get SDM values
SDM_Training = pd.read_sql_query("SELECT * FROM training", conn_SDM)

# Get and apply sales representative SK
SK_Sales_Representative = pd.read_sql_query("SELECT SALES_REPRESENTATIVE_SK, PK_SALES_REPRESENTATIVE FROM sales_representative WHERE VALID_TILL IS NULL", conn_DWH)
SK_Sales_Representative = SK_Sales_Representative.astype({"PK_SALES_REPRESENTATIVE": "float64"})
SDM_Training = SDM_Training.merge(SK_Sales_Representative, left_on="SALES_REPRESENTATIVE_CODE", right_on="PK_SALES_REPRESENTATIVE", how="left").drop(columns={"SALES_REPRESENTATIVE_CODE", "PK_SALES_REPRESENTATIVE"})

# Get DWH values
DWH_Training = pd.read_sql_query("SELECT * FROM training", conn_DWH)
DWH_Training = DWH_Training.astype({"YEAR": "int64", "COURSE_CODE": "int64"})

# Determine difference between SDM and DWH
diff = SDM_Training.merge(DWH_Training, indicator = True, how = "outer")
new = diff.loc[diff["_merge"] == "left_only"]
removed = diff.loc[diff["_merge"] == "right_only"]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute("DELETE FROM training WHERE YEAR = :YEAR AND SALES_REPRESENTATIVE_SK = :SALES_REPRESENTATIVE_SK AND COURSE_CODE = :COURSE_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij het verwijderen uit de database: {database} PK: {key}", database = "training",
                        key = f'{record["YEAR"]}-{record["SALES_REPRESENTATIVE_SK"]}-{record["COURSE_CODE"]}')

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute("INSERT INTO training (YEAR, SALES_REPRESENTATIVE_SK, COURSE_CODE) VALUES (:YEAR, :SALES_REPRESENTATIVE_SK, :COURSE_CODE)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij het inladen in database: {database} PK: {key}", database = "training",
                        key = f'{record["YEAR"]}-{record["SALES_REPRESENTATIVE_SK"]}-{record["COURSE_CODE"]}')
finally:
    cur.close()

conn_DWH.commit()
logger.info("tabel {tabel} geupdated, {new} rijen toegevoegd, {removed} rijen verwijderd",
            tabel = "training", new = len(new.index), removed = len(removed.index))

# Satisfaction - Type 1
# Get SDM values
SDM_Satisfaction = pd.read_sql_query("SELECT * FROM satisfaction", conn_SDM)

# Get and apply sales representative SK
SK_Sales_Representative = pd.read_sql_query("SELECT SALES_REPRESENTATIVE_SK, PK_SALES_REPRESENTATIVE FROM sales_representative WHERE VALID_TILL IS NULL", conn_DWH)
SK_Sales_Representative = SK_Sales_Representative.astype({"PK_SALES_REPRESENTATIVE": "float64"})
SDM_Satisfaction = SDM_Satisfaction.merge(SK_Sales_Representative, left_on="SALES_REPRESENTATIVE_CODE", right_on="PK_SALES_REPRESENTATIVE", how="left").drop(columns={"SALES_REPRESENTATIVE_CODE", "PK_SALES_REPRESENTATIVE"})

# Get DWH values
DWH_Satisfaction = pd.read_sql_query("SELECT * FROM satisfaction", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Satisfaction.merge(DWH_Satisfaction, indicator = True, how = "outer")
new = diff.loc[diff["_merge"] == "left_only"]
removed = diff.loc[diff["_merge"] == "right_only"]

cur = conn_DWH.cursor()

try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient = "records"):
        try:
            cur.execute("DELETE FROM satisfaction WHERE YEAR = :YEAR AND SALES_REPRESENTATIVE_SK = :SALES_REPRESENTATIVE_SK AND SATISFACTION_TYPE_CODE = :SATISFACTION_TYPE_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK: {key}", database = "satisfaction", key = f'{record["YEAR"]}-{record["SATISFACTION_TYPE_CODE"]}')

    # Insert new values
    for record in new.to_dict(orient = "records"):
        try:
            cur.execute("INSERT INTO satisfaction (YEAR, SALES_REPRESENTATIVE_SK, SATISFACTION_TYPE_CODE) VALUES (:YEAR, :SALES_REPRESENTATIVE_SK, :SATISFACTION_TYPE_CODE)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK: {key}", database = "satisfaction", key = f'{record["YEAR"]}-{record["SALES_REPRESENTATIVE_SK"]}-{record["SATISFACTION_TYPE_CODE"]}')
finally:
    cur.close()

conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel = "satisfaction", new=len(new.index), removed = len(removed.index))

# Product Forecast - Type 1
# Get SDM values
SDM_Product_Forecast = pd.read_sql_query("SELECT * FROM Product_Forecast", conn_SDM)

# Get DWH values
DWH_Product_Forecast= pd.read_sql_query("SELECT * FROM product_forecast", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Product_Forecast.merge(DWH_Product_Forecast, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM product_forecast WHERE PRODUCT_NUMBER = :PRODUCT_NUMBER AND YEAR = :YEAR AND MONTH = :MONTH", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="product_forecast", key=f'{record["PRODUCT_NUMBER"]}-{record["YEAR"]}-{record["MONTH"]}')

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO product_forecast VALUES (:PRODUCT_NUMBER, :YEAR, :MONTH, :EXPECTED_VOLUME)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="product_forecast", key=f'{record["PRODUCT_NUMBER"]}-{record["YEAR"]}-{record["MONTH"]}')
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="product_forecast", new=len(new.index), removed=len(removed.index))

# Inventory Level - Type 1
# Get SDM values
SDM_Inventory_Levels = pd.read_sql_query("SELECT * FROM Inventory_Levels", conn_SDM)

# Get DWH values
DWH_Inventory_Levels= pd.read_sql_query("SELECT * FROM inventory_levels", conn_DWH)

# Determine difference between SDM and DWH
diff = SDM_Inventory_Levels.merge(DWH_Inventory_Levels, indicator=True, how='outer')
new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()
try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient="records"):
        try:
            cur.execute(f"DELETE FROM inventory_levels WHERE PRODUCT_NUMBER = :PRODUCT_NUMBER AND INVENTORY_YEAR = :INVENTORY_YEAR AND INVENTORY_MONTH = :INVENTORY_MONTH", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK:{key}", database="inventory_levels", key=f'{record["PRODUCT_NUMBER"]}-{record["INVENTORY_YEAR"]}-{record["INVENTORY_MONTH"]}')

    # Insert new values
    for record in new.to_dict(orient="records"):
        try:
            cur.execute(f"INSERT INTO inventory_levels VALUES (:INVENTORY_YEAR, :INVENTORY_MONTH, :PRODUCT_NUMBER, :INVENTORY_COUNT)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database="inventory_levels", key=f'{record["PRODUCT_NUMBER"]}-{record["INVENTORY_YEAR"]}-{record["INVENTORY_MONTH"]}')
finally:
    cur.close()
conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel="inventory_levels", new=len(new.index), removed=len(removed.index))

# Sales Target - Type 1
# Get SDM values
SDM_Sales_Target = pd.read_sql_query("SELECT * FROM Sales_Target", conn_SDM)

# Get and apply sales representative SK
SK_Sales_Representative = pd.read_sql_query("SELECT SALES_REPRESENTATIVE_SK as SALES_REPRESENTATIVE, PK_SALES_REPRESENTATIVE FROM sales_representative WHERE VALID_TILL IS NULL", conn_DWH)
SK_Sales_Representative = SK_Sales_Representative.astype({"PK_SALES_REPRESENTATIVE": "float64"})
SDM_Sales_Target = SDM_Sales_Target.merge(SK_Sales_Representative, left_on="SALES_STAFF_CODE", right_on="PK_SALES_REPRESENTATIVE", how="left").drop(columns={"SALES_STAFF_CODE", "PK_SALES_REPRESENTATIVE"})

# Get and apply sales representative SK
SK_Customer_Site = pd.read_sql_query("SELECT CUSTOMER_STORE_SK, PK_RETAILER_SITE FROM customer_site WHERE VALID_TILL IS NULL", conn_DWH)
SK_Customer_Site = SK_Customer_Site.astype({"PK_RETAILER_SITE": "float64"})
SDM_Sales_Target = SDM_Sales_Target.merge(SK_Customer_Site, left_on="RETAILER_CODE", right_on="PK_RETAILER_SITE", how="left")

# We have sales target data for customer sites with PK's ranging from [168, 194]
# We do not have customer site data for those PK's
# Filter out data related to those customer sites, and do not attempt to load it in to the DWH
Nonexistent_Retailers = SDM_Sales_Target.loc[((SDM_Sales_Target["RETAILER_CODE"] > 167) & (SDM_Sales_Target["RETAILER_CODE"] < 195) & (SDM_Sales_Target["CUSTOMER_STORE_SK"].isna()))]

SDM_Sales_Target = SDM_Sales_Target.merge(Nonexistent_Retailers, how="outer", indicator=True)
SDM_Sales_Target = SDM_Sales_Target.loc[(SDM_Sales_Target["_merge"] == "left_only")]
SDM_Sales_Target = SDM_Sales_Target.drop(columns={"_merge", "RETAILER_CODE", "PK_RETAILER_SITE"}).rename(columns={"CUSTOMER_STORE_SK": "RETAILER_CODE"})

# Get DWH values
DWH_Sales_Target = pd.read_sql_query("SELECT * FROM sales_target", conn_DWH)
DWH_Sales_Target = DWH_Sales_Target.astype({"SALES_PERIOD": "float64", "RETAILER_CODE": "float64"})

# Determine difference between SDM and DWH
diff = SDM_Sales_Target.merge(DWH_Sales_Target, how = "outer", indicator = True)

new = diff.loc[(diff["_merge"] == "left_only")]
removed = diff.loc[(diff["_merge"] == "right_only")]

# Update DWH
cur = conn_DWH.cursor()

try:
    # Delete removed and updated values from DWH
    for record in removed.to_dict(orient = "records"):
        try:
            cur.execute("DELETE FROM sales_target WHERE SALES_REPRESENTATIVE = :SALES_REPRESENTATIVE AND SALES_YEAR = :SALES_YEAR AND SALES_PERIOD = :SALES_PERIOD AND PRODUCT_NUMBER = :PRODUCT_NUMBER AND RETAILER_CODE = :RETAILER_CODE", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij verwijderen uit database: {database} PK: {key}", database = "sales_target", key = f'{record["SALES_REPRESENTATIVE"]}-{record["SALES_YEAR"]}-{record["SALES_PERIOD"]}-{record["PRODUCT_NUMBER"]}-{record["RETAILER_CODE"]}')

    # Insert new values
    for record in new.to_dict(orient = "records"):
        try:
            cur.execute("INSERT INTO sales_target (SALES_REPRESENTATIVE, SALES_YEAR, SALES_PERIOD, RETAILER_NAME, PRODUCT_NUMBER, SALES_TARGET, RETAILER_CODE) VALUES (:SALES_REPRESENTATIVE, :SALES_YEAR, :SALES_PERIOD, :RETAILER_NAME, :PRODUCT_NUMBER, :SALES_TARGET, :RETAILER_CODE)", record)
        except sqlite3.Error as er:
            sqliteLogger.error(er)
            logger.info("Fout bij inladen in database: {database} PK:{key}", database = "sales_target", key = f'{record["SALES_REPRESENTATIVE"]}-{record["SALES_YEAR"]}-{record["SALES_PERIOD"]}-{record["PRODUCT_NUMBER"]}{record["RETAILER_CODE"]}')
finally:
    cur.close()

conn_DWH.commit()
logger.info("tabel {tabel} geüpdated, {new} rijen toegevoegd, {removed} rijen verwijderd", tabel = "sales_target", new=len(new.index), removed = len(removed.index))