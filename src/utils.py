import json
import os
import sys
import pysnooper
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def configure_logging():
    logger.add(
        "app.log",
        rotation="30 KB",
        retention=5,
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )
    logger.add(
        sys.stdout,
        format="<level>{time:HH:mm:ss} {level.name:.1} {function}:{line}</level> | {message}",
    )


configure_logging()


def getAbsPath(relPath):
    basepath = os.path.dirname(__file__)
    fullPath = os.path.abspath(os.path.join(basepath, relPath))

    return fullPath


def getConfig():
    configFileName = getAbsPath("../config.json")
    with open(configFileName) as config:
        config = json.loads(config.read())

    return config


def executeCommand(command):
    if type(command) == list:
        fullCommand = " ".join(command) + " &"
    else:
        fullCommand = command
    logger.log(f"About to execute command: {fullCommand}")
    os.system(fullCommand)
