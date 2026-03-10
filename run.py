# -*- coding: utf-8 -*-
"""
Created on Thu Jan  8 09:56:32 2026

@author: NBoyd1
"""

import os
import sys
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from decouple import config
from config import DevelopmentConfig, DevServerConfig
from app import create_app
from seed import seed

# Environment and Config setup
env = config('FLASK_ENV', default='development')

if env == "development":
    config_class = DevelopmentConfig
elif env == "dev_server":
    config_class = DevServerConfig
else:
    config_class = DevelopmentConfig

# Create app
app = create_app()
app.config.from_object(config_class)

# Db setup
connection_string = app.config['CONNECTION_STRING']
engine = create_engine(connection_string, echo=True)
Session = sessionmaker(bind=engine)
connect_src = app.config.get('CONNECT_SRC', None)


if __name__ == "__main__":
    app.run(debug=True)