import logging
import sys
import os
from logging.handlers import TimedRotatingFileHandler

class LoggerSetup:
    """
    Utility class to configure the application logger for RODSIC_Strat.
    Matches IB_Core logging system.
    """
    
    @staticmethod
    def get_logger(name: str):
        """
        Creates and configures a logger instance.
        Logs are saved to logs/RODSIC_Strat.log.
        Daily rotation moves old logs to logs/oldlogs/RODSIC_Strat_YYYYMMDD.log.
        """
        
        logger = logging.getLogger(name)
        
        # Default Level
        logger.setLevel(logging.INFO)
        
        # Ensure handlers are set up only once
        if not logger.handlers:
            # 1. Define Paths
            # Assume src/logger.py -> src/../logs -> logs/
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(base_dir, "logs")
            old_log_dir = os.path.join(log_dir, "oldlogs")
            
            os.makedirs(old_log_dir, exist_ok=True)
            
            log_file = os.path.join(log_dir, "RODSIC_Strat.log")
            
            # 2. Config Handler: TimedRotating
            # when='midnight': rotate at midnight
            # interval=1: every 1 day
            handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1)
            handler.suffix = "%Y%m%d" 
            handler.setLevel(logging.INFO)
            
            # Format
            formatter = logging.Formatter('%(asctime)s - %(name)s:%(lineno)d - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            
            # 3. Custom Namer
            def custom_namer(default_name):
                # default_name: .../logs/RODSIC_Strat.log.20240101
                path, filename = os.path.split(default_name)
                parts = filename.split('.')
                # Parts: ['RODSIC_Strat', 'log', '20240101']
                if len(parts) >= 3:
                    date_part = parts[-1]
                    new_filename = f"RODSIC_Strat_{date_part}.log"
                    return os.path.join(path, "oldlogs", new_filename)
                return default_name

            # 4. Custom Rotator
            def custom_rotator(source, dest):
                if os.path.exists(source):
                    os.rename(source, dest)

            handler.namer = custom_namer
            handler.rotator = custom_rotator
            
            logger.addHandler(handler)
            
            # Add Console Handler as well for Docker/Terminal visibility
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            
            logger.propagate = False 
            
        return logger
