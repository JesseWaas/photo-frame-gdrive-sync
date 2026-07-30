import logging 

from util import run, CmdError

logger = logging.getLogger(__name__)

TIMEOUT = 30

# ---------- Cycle power to attached USB hub by location ----------

class UsbHub:
    def __init__(self, location: str = None, vendor: str = None):
        self.location = location
        self.vendor = vendor

    def reset(self) -> bool:
        logger.warning("Attempting hub power cycle via uhubctl (location=%s)", self.location or "unspecified")

        try:
            # With proper udev rules and group membership (e.g., dialout),
            # uhubctl can be executed without sudo from the service user.
            args = ["uhubctl", "--action", "cycle"]

            if self.location:
                args += ["-l", self.location]

            if self.vendor:
                args += ["-n", self.vendor]

            run(args, timeout=TIMEOUT)
        except CmdError as e:
            logger.error("uhubctl power-cycle failed: %s", e)
            return False
        
        return True
