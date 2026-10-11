#!/usr/bin/env python3
"""
Empire Logger — Common logging module for all solo-empire scripts.

Provides consistent logging across all scripts with:
  - File + console output
  - Structured format: [YYYY-MM-DD HH:MM:SS] [LEVEL] [script_name] message
  - Per-script log files in data/logs/
  - Optional JSON logging for machine consumption
  - Rotation support (keeps last 30 days)

Usage (in other scripts):
    from empire_logger import get_logger
    
    logger = get_logger('my_script')
    logger.info('Starting process')
    logger.warning('Something unexpected')
    logger.error('Something failed')
    
    # With JSON output
    logger.json({'revenue': 10000, 'status': 'ok'})
"""

import logging
import os
import sys
import json
from datetime import datetime

LOGS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'logs')
LOG_FORMAT = '[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


class JsonFormatter(logging.Formatter):
    """Output logs as JSON lines for machine consumption."""
    def format(self, record):
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).strftime(DATE_FORMAT),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        if hasattr(record, 'json_data'):
            log_entry['data'] = record.json_data
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


class EmpireLogger(logging.Logger):
    """Extended logger with JSON data support."""
    def json(self, data, message=''):
        """Log structured JSON data."""
        extra = {'json_data': data}
        msg = message or json.dumps(data)
        self.info(msg, extra=extra)


def get_logger(name, log_file=None, level=logging.INFO, json_mode=False):
    """
    Get a configured logger for a script.
    
    Args:
        name: Script name (used for log file and logger name)
        log_file: Custom log file path (default: data/logs/{name}.log)
        level: Logging level (default: INFO)
        json_mode: If True, also output JSON lines to {name}.jsonl
    
    Returns:
        Configured Logger instance
    """
    # Register custom logger class
    logging.setLoggerClass(EmpireLogger)
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(console)
    
    # File handler
    if log_file is None:
        log_file = os.path.join(LOGS_DIR, f'{name}.log')
    
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(file_handler)
    
    # Optional JSON handler
    if json_mode:
        json_file = os.path.join(LOGS_DIR, f'{name}.jsonl')
        json_handler = logging.FileHandler(json_file, encoding='utf-8')
        json_handler.setLevel(level)
        json_handler.setFormatter(JsonFormatter())
        logger.addHandler(json_handler)
    
    return logger


def cleanup_old_logs(days=30):
    """Remove log files older than N days."""
    if not os.path.exists(LOGS_DIR):
        return 0
    
    cutoff = datetime.now().timestamp() - (days * 86400)
    removed = 0
    
    for filename in os.listdir(LOGS_DIR):
        filepath = os.path.join(LOGS_DIR, filename)
        if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
            os.remove(filepath)
            removed += 1
    
    return removed


def log_stats():
    """Show logging statistics."""
    if not os.path.exists(LOGS_DIR):
        print('No logs directory found.')
        return
    
    total_size = 0
    file_count = 0
    
    print('📋 Log Files:')
    print(f'{"File":<40} {"Size":>10} {"Modified"}')
    print('-' * 70)
    
    for filename in sorted(os.listdir(LOGS_DIR)):
        filepath = os.path.join(LOGS_DIR, filename)
        if os.path.isfile(filepath):
            size = os.path.getsize(filepath)
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath)).strftime('%Y-%m-%d %H:%M')
            total_size += size
            file_count += 1
            print(f'{filename:<40} {size/1024:>8.1f} KB  {mtime}')
    
    print(f'\nTotal: {file_count} files, {total_size/1024:.1f} KB')


if __name__ == '__main__':
    # Self-test
    logger = get_logger('empire_logger_test')
    logger.info('Logger initialized')
    logger.warning('Test warning')
    logger.error('Test error')
    logger.json({'test': True, 'count': 42}, 'JSON data logged')
    print()
    log_stats()
