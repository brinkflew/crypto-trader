"""
Custom logging
"""

import io
import os
import re
import atexit
import pprint
import logging
import logging.handlers

from blessed import Terminal
from getpass import getpass
from discordwebhook import Discord

from trader import Config


term = Terminal()
pretty = pprint.PrettyPrinter(indent=4).pformat

# Find more info about colors and styles in Blessed"s documentation!
# Styles: https://blessed.readthedocs.io/en/latest/terminal.html#styles
# Colors: https://blessed.readthedocs.io/en/latest/colors.html#color-chart

STYLE_RESET = term.normal

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
ANSI_BOLD_ESCAPE = re.compile(r"(?:\x1b\[1m)(.+?)(?=\x1b\(B\x1b\[m)")

LOG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../.logs/trader.log')
LOG_FORMAT_RAW = "%(asctime)s [%(levelname)s] %(message)s"
LOG_FORMAT = (
    f"{term.darkgray}%(asctime)s "
    f"{STYLE_RESET}%(log_color)s[%(symbol)s] "
    f"{STYLE_RESET}%(message)s"
)
LOG_DATEFORMAT = "%Y-%m-%d %H:%M:%S"
LOG_LEVELS = {
    "SUCCESS": 25,
    "QUESTION": 100,
}
LOG_SYMBOLS = {
    "NOTSET":   ("#", term.darkgray_bold, 0),
    "DEBUG":    ("%", term.steelblue4_bold, 0),
    "INFO":     ("i", term.cyan3_bold, 6211540),
    "SUCCESS":  ("+", term.darkolivegreen3_bold, 11523935),
    "QUESTION": ("?", term.plum3_bold, 0),
    "WARNING":  ("!", term.bright_yellow_bold, 13088377),
    "ERROR":    ("-", term.lightcoral_bold, 16746375),
    "CRITICAL": ("~", term.lightcoral_bold, 16746375),
}

LOG_ICONS_BASE_URL = "https://github.com/brinkflew/crypto-trade/blob/master/.media/notifications/{level}.png?raw=true"


atexit.register(lambda: print(STYLE_RESET))


class LogRecord(logging.LogRecord):
    """
    Custom implementation of LogRecord to add format specifiers
    """

    def __init__(self, name, level, pathname, lineno, msg, args, exc_info, func=None, sinfo=None, **kwargs):
        super().__init__(name, level, pathname, lineno, msg, args, exc_info, func=func, sinfo=sinfo, **kwargs)
        self.symbol = LOG_SYMBOLS.get(self.levelname, LOG_SYMBOLS["NOTSET"])[0]
        self.embed = {}


class CustomLogger(logging.getLoggerClass()):
    """
    Custom implementation of logger, adding a few methods to transform it
    into a usable tool for interacting with the user
    """

    def __init__(self, *args, **kwargs):
        self.discord_enabled = False
        super().__init__(*args, **kwargs)

    def makeRecord(self, name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None):
        rv = LogRecord(name, level, fn, lno, msg, args, exc_info, func, sinfo)

        if extra is not None:
            for key in extra:
                if (key in ["message", "asctime"]) or (key in rv.__dict__):
                    raise KeyError("Attempt to overwrite %r in LogRecord" % key)
                rv.__dict__[key] = extra[key]
        return rv

    def format_question(self, question, choices=None, default=None, trailing=" ", choices_sep="/"):
        text_parts = [question]
        if isinstance(choices, (list, tuple)):
            choices = choices_sep.join(choices)
        if choices:
            text_parts.append(f"[{choices}]")
        if default:
            text_parts.append(f"({default})")

        _mem_logger_trap = io.StringIO()
        _mem_logger = getLogger(name="virtual", trap=_mem_logger_trap)
        _mem_logger.log(getattr(logging, "QUESTION"), " ".join(text_parts) + trailing)
        message = _mem_logger_trap.getvalue()
        _mem_logger_trap.close()
        return message.rstrip("\n")

    # Implemented later by `add_logging_level`,
    # included for IDE autocompletion/intellisense
    def success(self, message, *args, **kwargs):
        """
        Logs a success message
        """
        self.log(LOG_LEVELS["SUCCESS"], message, *args, **kwargs)

    def confirm(self, question):
        """
        Asks the user to enter Y or N (case-insensitive).
        """

        choices = ["y", "n"]
        answer = ""
        message = self.format_question(question, choices=choices)
        while answer not in choices:
            answer = input(message)[0].lower()
        return answer == "y"

    def ask(self, question, default=None):
        """
        Asks something to the user.
        """
        message = self.format_question(question, default=default)
        answer = input(message)
        if default and not answer:
            return default
        return answer

    def password(self, question, default=None):
        """
        Asks for a password.
        """
        answer: str = getpass(
            self.format_question(question, default="unchanged" if default else None)
        )
        if default and not answer:
            return default
        return answer

    def over(self, message, level="INFO", end="\r"):
        """
        Formats a message and prints it to STDOUT as if it was to be logged
        without actually logging it.
        """
        _mem_logger.log(getattr(logging, level), message)
        print(_mem_logger_trap.getvalue().rstrip("\n"), end=end)
        _mem_logger_trap.truncate(0)


def add_logging_level(name, value, method_name=None):
    if method_name is None:
        method_name = name.lower()

    def log_for_level_method(self, msg, *args, **kwargs):
        nonlocal value
        if self.isEnabledFor(value):
            self._log(value, msg, args, **kwargs)

    setattr(logging, name, value)
    logging.addLevelName(value, name)
    setattr(logging.getLoggerClass(), method_name, log_for_level_method)


class StandardFormatter(logging.Formatter):
    """
    Custom log formatter class to format logs in file and console output
    """

    def __init__(self, fmt=None, *args, **kwargs):
        fmt = fmt or LOG_FORMAT_RAW
        kwargs.setdefault("datefmt", LOG_DATEFORMAT)
        super().__init__(fmt, *args, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        if record.levelname in LOG_SYMBOLS:
            record.__dict__["symbol"] = LOG_SYMBOLS[record.levelname][0]

        return super().format(record)


class ColorFormatter(StandardFormatter):
    """
    Custom log formatter class to colorize log levels in console output
    """

    def __init__(self, fmt=None, *args, **kwargs):
        fmt = fmt or LOG_FORMAT
        kwargs.setdefault("datefmt", LOG_DATEFORMAT)
        super().__init__(fmt, *args, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        if record.levelname in LOG_SYMBOLS:
            record.__dict__["log_color"] = LOG_SYMBOLS[record.levelname][1]

        if record.levelname == "CRITICAL" or (record.levelname == "ERROR" and record.__dict__.get("exc_info")):
            record.msg = str(record.msg)
            record.msg = record.msg[0].upper() + record.msg[1:]
            record.msg = f"{term.lightcoral}{str(record.msg)}{STYLE_RESET}"

        return "\x1B[2K" + super().format(record)


class FileFormatter(ColorFormatter):
    """
    Custom formatter implementation stripping ANSI characters to write to a file
    """

    def __init__(self, fmt=None, *args, **kwargs):
        fmt = fmt or LOG_FORMAT_RAW
        super().__init__(fmt, *args, **kwargs)

    def format(self, record: LogRecord):
        return ANSI_ESCAPE.sub("", super().format(record)).replace("\x1B(B", "")


class DiscordFormatter(ColorFormatter):
    """
    Custom log formatter for Discord notifications using embeds
    """

    def format(self, record):
        if record.levelname in LOG_SYMBOLS:
            record.__dict__["log_color"] = LOG_SYMBOLS[record.levelname][2]

        record.embed = {
            "color": getattr(record, "log_color"),
            "author": {
                "name": record.levelname[0].upper() + record.levelname[1:].lower(),
                "icon_url": LOG_ICONS_BASE_URL.replace("{level}", record.levelname.lower()),
            },
        }

        if isinstance(record.msg, dict):
            record.embed.update(record.msg)  # type: ignore
        else:
            message = str(record.msg)
            message = ANSI_BOLD_ESCAPE.sub(r"{bold}\1{bold}".format(bold="**"), message)
            message = ANSI_ESCAPE.sub("", message).replace("\x1B(B", "")

            if record.levelname == "CRITICAL" or (record.levelname == "ERROR" and record.__dict__.get("exc_info")):
                message = message[0].upper() + message[1:]

                if record.__dict__.get("exc_text"):
                    message += (
                        "\n```\n"
                        f"{record.__dict__.get('exc_text')}"
                        "\n```"
                    )

            record.embed["description"] = message  # type: ignore

        return record.embed  # type: ignore


class DiscordHandler(logging.StreamHandler):
    """
    Log information using Discord webhooks
    """
    def __init__(self, webhook):
        super().__init__()
        self.discord = Discord(url=webhook)

    def emit(self, record):
        return self.discord.post(embeds=[self.format(record)])


def getLogger(name=None, trap=None, exclude_handlers=set()) -> CustomLogger:
    """
    Creates a supercharged logger instance
    """
    logging_class = logging.getLoggerClass()
    logging._acquireLock()  # type: ignore

    try:
        logging.setLoggerClass(CustomLogger)
        logger = logging.getLogger(name)
        logging.setLoggerClass(logging_class)

        logger.propagate = False

        for level, value in LOG_LEVELS.items():
            add_logging_level(level, value)

        if not logger.hasHandlers() or trap is not None:
            for handler in logger.handlers:
                logger.removeHandler(handler)

            if 'file' not in exclude_handlers:
                os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
                file_handler = logging.FileHandler(os.path.realpath(LOG_FILE))
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(FileFormatter())
                logger.addHandler(file_handler)

            if 'console' not in exclude_handlers:
                log_handler = logging.StreamHandler(trap)
                log_handler.setLevel(logging.INFO)
                log_handler.setFormatter(ColorFormatter())
                logger.addHandler(log_handler)

            if 'discord' not in exclude_handlers:
                config = Config()

                if config.DISCORD_WEBHOOK_URL is not None:
                    logger.discord_enabled = True  # type: ignore
                    discord_handler = DiscordHandler(webhook=config.DISCORD_WEBHOOK_URL)
                    discord_handler.setLevel("SUCCESS")
                    discord_handler.setFormatter(DiscordFormatter())
                    logger.addHandler(discord_handler)

        setattr(logging.getLoggerClass(), 'over', CustomLogger.over)

        return logger  # type: ignore
    finally:
        logging._releaseLock()  # type: ignore


def set_log_level(level, logger=None) -> None:
    """
    Set the log level for the base logger
    :param level: the level to set for the logger as a string
    """
    global _root_logger
    _logger = logger or _root_logger
    _logger.setLevel(level)


_root_logger = getLogger()
_mem_logger_trap = io.StringIO()
_mem_logger = getLogger(name="virtual", trap=_mem_logger_trap)

logger = getLogger(name="trader")
"""
Log to the console, a logfile and Discord (if enabled).
"""

discord_logger = getLogger(name="discord", exclude_handlers={'file', 'console'})
"""
Log to Discord only (if enabled).
"""

for handler in discord_logger.handlers:
    handler.setLevel(logging.INFO)

set_log_level(logging.WARNING, _root_logger)
set_log_level(logging.INFO, _mem_logger)
set_log_level(logging.DEBUG, logger)
set_log_level(logging.INFO, discord_logger)
