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
"""Manages dataclass for the solr possible conflict doc."""
from dataclasses import dataclass
from datetime import datetime

from namex_solr_api.common.base_enum import BaseEnum

from .name import Name


class PCField(BaseEnum):
    """Enum of the possible conflict fields available."""

    # unique key for all docs
    UNIQUE_KEY = "id"
    # stored fields
    CORP_NUM = "corp_num"
    CORP_START_DATE = "corp_start_date"
    JURISDICTION = "jurisdiction"
    NAMES = "names"
    NR_NUM = "nr_num"
    NR_START_DATE = "nr_start_date"
    STATE = "state"
    # TODO: query fields
    
    # common built in across docs
    SCORE = "score"


@dataclass
class PossibleConflict:
    """Class representation for a solr possible conflict doc."""
    id: str
    jurisdiction: str
    names: list[Name]
    state: str
    corp_num: str | None = None
    corp_start_date: datetime | None = None
    nr_num: str | None = None  # TODO: confirm if Exraprovincial or Federal have an NR?
    nr_start_date: datetime | None = None
