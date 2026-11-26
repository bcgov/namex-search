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
"""Base db connection class using namex-solr-api cloud SQL utilities."""

from cloud_sql_connector import (
    DBConfig,
    TCPDBConfig,
    setup_pg8000_close_event_listener,
    setup_search_path_event_listener,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import scoped_session, sessionmaker


class BaseDB:
    """Database connection wrapper supporting both Cloud SQL Connector and TCP modes."""

    def __init__(self, db_config: DBConfig | TCPDBConfig, test_connection=True):
        """Initialize the db connection.

        Args:
            db_config: Database configuration (DBConfig for Cloud SQL Connector or TCPDBConfig for TCP)
            test_connection: Whether to test the connection after initialization
        """
        engine_options = db_config.get_engine_options()

        if isinstance(db_config, TCPDBConfig):
            self.engine = create_engine("postgresql://", **engine_options)
        else:
            self.engine = create_engine("postgresql+pg8000://", **engine_options)

        setup_pg8000_close_event_listener(self.engine)
        if hasattr(db_config, "schema"):
            setup_search_path_event_listener(self.engine, db_config.schema)

        if test_connection:
            self._test_connection()

    def _test_connection(self):
        """Test the database connection."""
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT version()")).fetchone()
            print(f"Connection successful. Database version: {result[0]}")  # noqa: T201

    def teardown(self):
        """Clean up resources."""
        self.engine.dispose()

    @property
    def session(self):
        """Provide a database session."""
        return scoped_session(sessionmaker(bind=self.engine))
