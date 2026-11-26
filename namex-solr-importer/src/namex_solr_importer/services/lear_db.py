# Copyright © 2025 Province of British Columbia
#
# Licensed under the BSD 3 Clause License, (the "License");
# you may not use this file except in compliance with the License.
# The template for the license can be found here
#    https://opensource.org/license/bsd-3-clause/
#
# Redistribution and use in source and binary forms,
# with or without modification, are permitted provided that the
# following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS “AS IS”
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""Create lear db connection."""

from cloud_sql_connector import DBConfig, TCPDBConfig
from flask import Flask

from namex_solr_importer.services.base_db import BaseDB


class LearDB:  # pylint: disable=duplicate-code
    """LEAR db connection (auto-detects Cloud SQL Connector vs TCP mode)."""

    def __init__(self, app: Flask | None = None):
        self.db = None
        if app:
            self.init_app(app)

    def init_app(self, app: Flask, test_connection=True):
        """Initialize with option to skip connection test.

        Auto-detects connection mode:
        - If LEAR_DB_CONNECTION_NAME is set → Cloud SQL Connector mode (backward compatible)
        - If LEAR_DB_HOST is set → TCP mode (bastion/SSH tunnel)
        """
        try:
            # Auto-detect mode based on what's configured
            connection_name = app.config.get("LEAR_DB_CONNECTION_NAME")
            db_host = app.config.get("LEAR_DB_HOST")

            if connection_name:
                # Cloud SQL Connector mode (original behavior - backward compatible)
                db_config = DBConfig(
                    instance_name=connection_name,
                    database=app.config["LEAR_DB_NAME"],
                    user=app.config["LEAR_DB_USER"],
                    ip_type="public",
                    enable_iam_auth=True,
                    schema=app.config.get("LEAR_DB_SCHEMA", ""),
                )
            elif db_host:
                # TCP mode (bastion/SSH tunnel with IAM token auth)
                db_config = TCPDBConfig(
                    database=app.config["LEAR_DB_NAME"],
                    user=app.config["LEAR_DB_USER"],
                    host=db_host,
                    port=int(app.config.get("LEAR_DB_PORT", "5432")),
                    enable_iam_auth=True,
                    credentials_file=app.config.get(
                        "GOOGLE_APPLICATION_CREDENTIALS_LEAR"
                    ),
                    schema=app.config.get("LEAR_DB_SCHEMA", ""),
                )
            else:
                raise ValueError(
                    "Either LEAR_DB_CONNECTION_NAME (Cloud SQL Connector) or LEAR_DB_HOST (TCP/bastion) must be set"
                )

            self.db = BaseDB(db_config, test_connection)

        except Exception as e:
            app.logger.error(f"LEAR db init failed: {e!s}")
            if self.db:
                self.db.teardown()
            raise

