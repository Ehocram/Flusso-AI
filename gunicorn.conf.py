"""Configurazione gunicorn per il deploy on-prem."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
timeout = 60
accesslog = "-"   # stdout -> raccoglibile dal SIEM
errorlog = "-"
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s "%(a)s" %(L)ss'
