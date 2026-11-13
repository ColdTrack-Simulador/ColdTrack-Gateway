from __future__ import annotations

import os

import database
from hmac import compare_digest
from pathlib import Path

import click
from flask import Flask, g, jsonify, redirect, request, send_from_directory, session, url_for
from smtplib import SMTPException
from werkzeug.security import check_password_hash

from coldtrack.accounts import Accounts
from coldtrack.accounts_http import create_accounts_blueprint
from coldtrack.application import Operations, secret_digest
from coldtrack.assistant import create_assistant_blueprint
from coldtrack.equipment_http import create_equipment_blueprint
from coldtrack.http import create_operations_blueprint
from coldtrack.infrastructure import SQLiteRepository, WerkzeugPasswords
from coldtrack.openapi import configure_swagger
from coldtrack.notifications import create_notifications_blueprint
