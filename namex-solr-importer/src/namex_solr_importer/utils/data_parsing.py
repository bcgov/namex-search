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
"""Data parsing functions."""

from datetime import datetime

from namex_solr_api.services.namex_solr.doc_models import Name, PossibleConflict


def _parse_names(data: dict, conflict_type: str) -> list[Name]:
    """Parse the name data as a list of Name."""
    if conflict_type == "CORP":
        return [Name(name=data["name"], name_state="CORP")]

    names: list[Name] = []
    for name_data in data["names"]:
        names.append(
            Name(
                name=name_data["name"],
                name_state=name_data["name_state"],
                submit_count=name_data["submit_count"],
                choice=name_data.get("choice"),
            )
        )
    return names


def parse_conflict(data: dict, conflict_type: str) -> PossibleConflict:
    """Parse the data as a PossibleConflict."""
    converted_start_date = None
    if start_date := data.get("start_date"):
        converted_start_date = datetime.isoformat(
            start_date, timespec="seconds"
        ).replace("+00:00", "")
    nr_num = "".join(data["nr_num"].split()) if data.get("nr_num") else None
    return PossibleConflict(
        id=nr_num if conflict_type == "NR" else data["corp_num"],
        names=_parse_names(data, conflict_type),
        state=data["state"],
        type=conflict_type,
        sub_type=data.get("sub_type"),
        corp_num=data.get("corp_num"),
        jurisdiction=data.get("jurisdiction") or "BC",
        nr_num=nr_num,
        start_date=converted_start_date,
    )


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_synonyms(data: list) -> dict[str, list[str]]:
    """Parse the synonym data in preparation for namex solr api update call."""
    parsed_synonyms = {}
    for row in data:
        members = _split_csv(row[0] if row else None)
        if not members:
            continue
        stems = _split_csv(row[1] if len(row) > 1 else None)
        for key in [members[0], *stems]:
            parsed_synonyms[key] = members
    return parsed_synonyms
