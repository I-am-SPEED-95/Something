import sqlite3
from Logging import logger, sqliteLogger

RIM_SCRIPT_PATH = "../DWH_RIM.txt"
SDM_PATH = "../../SDM/GO_SDM.db"
DWH_PATH = "data/GO_DWH.db"

__conn_SDM = sqlite3.connect(SDM_PATH)
__conn_DWH = sqlite3.connect(DWH_PATH)
__conn_DWH.set_trace_callback(sqliteLogger.info)
__conn_DWH.execute("PRAGMA foreign_keys = 1")

def __create_dwh_db():
    __conn_DWH.set_trace_callback(None)

    with open(RIM_SCRIPT_PATH, "r") as f:
        sql = f.read()

    try:
        __conn_DWH.executescript(sql)
        __conn_DWH.commit()
        logger.info("DWH database aangemaakt op basis van RIM script")
    except Exception as e:
        logger.error(f"fout bij aanmaken DWH database: {e}")
        raise


    __conn_DWH.set_trace_callback(sqliteLogger.info)

def connections():
    # Als er geen tabellen bestaan in de DB, run dan het DWH script
    if __conn_DWH.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0:
        __create_dwh_db()

    return __conn_DWH, __conn_SDM