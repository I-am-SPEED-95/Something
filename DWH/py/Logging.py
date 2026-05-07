from loguru import logger

def is_sqlite(log_record):
    return log_record["extra"].get("sqlite") is True

logger.remove()
logger.add("logs/DWH.log", level="DEBUG", filter=lambda r: not is_sqlite(r))
logger.add("logs/DWH_SQlite.log", level="INFO", filter=is_sqlite, rotation="10 MB")

sqliteLogger = logger.bind(sqlite= True)