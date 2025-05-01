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
"""Manages solr synonym lists (used for prepping solr queries over synonym fields)."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import auto

from sqlalchemy import Column, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from namex_solr_api.common.base_enum import BaseEnum
from .base import Base
from .db import db


class SolrSynonymList(Base):
    """Used to hold solr synonym information."""
    
    class Type(BaseEnum):
        """Enum of the solr synonym types."""

        ADDRESS = auto()
        NAME = auto()

    __tablename__ = "solr_synonym_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    synonym: Mapped[str] = mapped_column(String(50), index=True)
    synonym_list = Column(JSONB, nullable=False)
    synonym_type: Mapped[Type] = mapped_column(default=Type.NAME, index=True)
    last_update_date: Mapped[datetime] = mapped_column(default=func.now())

    @classmethod
    def find_by_synonym(cls, synonym: str, synonym_type: Type) -> SolrSynonymList:
        """Return all the solr synonym objects for synonyms including the given phrase/word."""
        return cls.query.filter_by(synonym=synonym.lower(), synonym_type=synonym_type).one_or_none()

    @classmethod
    def find_all_beginning_with_phrase(cls, phrase: str, synonym_type: Type) -> SolrSynonymList:
        """Return all the solr synonym objects for synonyms including the given phrase/word."""
        return cls.query.filter_by(synonym_type=synonym_type).filter(cls.synonym.ilike(f"{phrase}%")).all()

    @staticmethod
    def create_or_replace_all(synonyms: dict[str, list[str]], synonym_type: Type):
        """Add or replace the given synonyms inside the db."""
        for synonym, synonym_list in synonyms.items():
            if synonym_list_record := SolrSynonymList.find_by_synonym(synonym, synonym_type):
                synonym_list_record.synonym_list = synonym_list
                synonym_list_record.last_update_date = datetime.now(UTC)
                db.session.add(synonym_list_record)
            else:
                db.session.add(
                    SolrSynonymList(synonym=synonym, synonym_list=synonym_list, synonym_type=synonym_type)
                )
        db.session.commit()
