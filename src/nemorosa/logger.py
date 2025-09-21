import threading
import traceback

from colorama import Fore, Style


class ColorLogger:
    def __init__(self, loglevel="info"):
        levels = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
        self.level = levels.get(loglevel.lower(), 1)
        self.colors = {
            "success": Fore.GREEN,
            "header": Fore.YELLOW,
            "section": Fore.CYAN,
            "prompt": Fore.MAGENTA,
            "error": Fore.RED,
            "critical": Fore.RED,
            "debug": Fore.BLUE,
            "info": Style.RESET_ALL,
            "warning": Fore.YELLOW,
        }

    def _should_log(self, level_name):
        level_map = {
            "debug": 0,
            "info": 1,
            "warning": 2,
            "error": 3,
            "critical": 4,
            "success": 1,
            "header": 1,
            "section": 1,
            "prompt": 1,
        }
        return level_map.get(level_name, 1) >= self.level

    def _log(self, level_name, msg, *args):
        if not self._should_log(level_name):
            return

        color = self.colors.get(level_name, Style.RESET_ALL)
        formatted_msg = msg % args if args else msg
        print(f"{color}{formatted_msg}{Style.RESET_ALL}")

    def success(self, msg, *args, **kwargs):
        self._log("success", msg, *args)

    def header(self, msg, *args, **kwargs):
        self._log("header", msg, *args)

    def section(self, msg, *args, **kwargs):
        self._log("section", msg, *args)

    def prompt(self, msg, *args, **kwargs):
        self._log("prompt", msg, *args)

    def error(self, msg, *args, **kwargs):
        self._log("error", msg, *args)

    def critical(self, msg, *args, **kwargs):
        self._log("critical", msg, *args)

    def info(self, msg, *args, **kwargs):
        self._log("info", msg, *args)

    def debug(self, msg, *args, **kwargs):
        self._log("debug", msg, *args)

    def warning(self, msg, *args, **kwargs):
        self._log("warning", msg, *args)

    def exception(self, msg, *args, **kwargs):
        self.error(msg, *args)
        if "exc_info" in kwargs and kwargs["exc_info"]:
            traceback.print_exc()


def generate_logger(loglevel="info"):
    return ColorLogger(loglevel)


# Global logger instance
_logger_instance: ColorLogger | None = None
_logger_lock = threading.Lock()


def get_logger() -> ColorLogger:
    """Get global logger instance.

    Returns:
        ColorLogger: Logger instance.
    """
    global _logger_instance
    with _logger_lock:
        if _logger_instance is None:
            _logger_instance = generate_logger()
        return _logger_instance


def set_logger(logger: ColorLogger) -> None:
    """Set global logger instance.

    Args:
        logger: Logger instance to set as current.
    """
    global _logger_instance
    with _logger_lock:
        _logger_instance = logger
