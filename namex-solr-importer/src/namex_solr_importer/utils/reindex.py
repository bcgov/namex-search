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
"""Manages util methods for reindexing."""
from datetime import UTC, datetime
from http import HTTPStatus
from time import sleep

from flask import current_app

from namex_solr_api.exceptions import SolrException
from namex_solr_api.services import solr


def get_replication_detail(field: str, leader: bool):
    """Return the replication detail for the core."""
    details: dict = (solr.replication("details", leader)).json()["details"]
    # remove data unwanted in the logs
    if field != "commits" and "commits" in details:
        del details["commits"]
    if not leader and field != "leaderDetails" and "leaderDetails" in details["follower"]:
        del details["follower"]["leaderDetails"]

    # log full details and return data element
    current_app.logger.debug("Full replication details: %s", details)
    if leader:
        return details.get(field)
    return details["follower"].get(field)


def reindex_prep():
    """Execute reindex operations needed before index is reloaded."""
    # backup leader index
    backup_trigger_time = (datetime.now(UTC)).replace(tzinfo=UTC)
    backup = solr.replication("backup", True)
    current_app.logger.debug(backup.json())
    if current_app.config.get("HAS_FOLLOWER", True):
        # disable follower polling during reindex
        disable_polling = solr.replication("disablepoll", False)
        current_app.logger.debug(disable_polling.json())
    # await 60 seconds in case a poll was in progress and to give time for backup to complete
    current_app.logger.debug("Pausing 60s for SOLR to complete reindex prep...")
    sleep(60)
    # verify current backup is from just now and was successful in case of failure
    current_app.logger.debug("Verifying SOLR reindex prep...")
    backup_succeeded = False
    for i in range(20):
        current_app.logger.debug(f"Checking new backup {i + 1} of 20...")
        if backup_detail := get_replication_detail("backup", True):
            backup_start_time = datetime.fromisoformat(backup_detail["startTime"])
            if backup_detail["status"] == "success" and backup_trigger_time < backup_start_time:
                backup_succeeded = True
                break
        # retry repeatedly (new backup in prod will take a couple minutes)
        sleep(30 + (i*2))
    if not backup_succeeded:
        raise SolrException("Failed to backup leader index", HTTPStatus.INTERNAL_SERVER_ERROR)
    current_app.logger.debug("Backup succeeded. Checking polling disabled...")
    if current_app.config.get("HAS_FOLLOWER", True):
        # verify follower polling disabled so it doesn't update until reindex is complete
        is_polling_disabled = get_replication_detail("isPollingDisabled", False)
        if not bool(is_polling_disabled):
            current_app.logger.debug("is_polling_disabled: %s", is_polling_disabled)
            raise SolrException("Failed disable polling on follower",
                                str(is_polling_disabled),
                                HTTPStatus.INTERNAL_SERVER_ERROR)
        current_app.logger.debug("Polling disabled. Disabling leader replication...")
        # disable leader replication for reindex duration (important to do this after polling disabled)
        disable_replication = solr.replication("disablereplication", True)
        current_app.logger.debug(disable_replication.json())

    # delete existing index
    current_app.logger.debug("REINDEX_CORE set: deleting current solr index...")
    solr.delete_all_docs()


def reindex_post():
    """Execute post reindex operations on the follower index."""
    if current_app.config.get("HAS_FOLLOWER", True):
        # reenable leader replication
        enable_replication = solr.replication("enablereplication", True)
        current_app.logger.debug(enable_replication.json())
        sleep(5)
        # force the follwer to fetch the new index
        fetch_new_idx = solr.replication("fetchindex", False)
        current_app.logger.debug(fetch_new_idx.json())
        sleep(10)
        # renable polling
        enable_polling = solr.replication("enablepoll", False)
        current_app.logger.debug(enable_polling.json())


def reindex_recovery():
    """Restore the index on the leader and renable polling on the follower."""
    restore = solr.replication("restore", True)
    current_app.logger.debug(restore.json())
    current_app.logger.debug("awaiting restore completion...")
    for i in range(100):
        current_app.logger.debug(f"Checking restore status ({i + 1} of 100)...")
        status = solr.replication("restorestatus", True)
        current_app.logger.debug(status)
        current_app.logger.debug(status.json())
        if (status.json())["restorestatus"]["status"] == "success":
            current_app.logger.debug("restore complete.")
            enable_replication = solr.replication("enablereplication", True)
            current_app.logger.debug(enable_replication.json())
            sleep(5)
            enable_polling = solr.replication("enablepolling", False)
            current_app.logger.debug(enable_polling.json())
            return
        if (status.json())["status"] == "failed":
            break
        sleep(10 + (i*2))
    current_app.logger.error("Possible failure to restore leader index. Manual intervention required.")
