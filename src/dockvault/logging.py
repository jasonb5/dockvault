# src/dockvault/logging.py
import logging.config


LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        },
        "access": {
            "format": '%(asctime)s %(levelname)-8s [%(name)s] %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
        "access_console": {
            "class": "logging.StreamHandler",
            "formatter": "access",
        },
    },
    "loggers": {
        "dockvault": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "apscheduler": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.error": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": ["access_console"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}


def setup_logging() -> None:
    logging.config.dictConfig(LOGGING_CONFIG)
