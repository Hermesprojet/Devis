"""Erreurs stables des contrats documentaires."""

from __future__ import annotations


class ContractError(ValueError):
    """Erreur de contrat sérialisable sans donnée documentaire sensible."""

    code = "document_contract_error"

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": self.message}
        if self.field is not None:
            result["field"] = self.field
        return result


class InvalidIdentifierError(ContractError):
    code = "invalid_document_identifier"


class InvalidConfidenceError(ContractError):
    code = "invalid_document_confidence"


class InvalidBoundingBoxError(ContractError):
    code = "invalid_document_bounding_box"


class InvalidCitationError(ContractError):
    code = "invalid_document_citation"


class InvalidStructuredDataError(ContractError):
    code = "invalid_document_structured_data"


class InvalidVersionError(ContractError):
    code = "invalid_document_version"


class InvalidContractValueError(ContractError):
    code = "invalid_document_contract_value"
