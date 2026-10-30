class WavebirdError(Exception):
    exit_code = 1


class ConfigurationError(WavebirdError):
    exit_code = 2


class AuthenticationError(WavebirdError):
    exit_code = 3


class QuotaError(WavebirdError):
    exit_code = 4


class InvalidTickerError(WavebirdError):
    exit_code = 5


class TransportError(WavebirdError):
    exit_code = 6


class ProviderResponseError(WavebirdError):
    exit_code = 7


class ProviderSchemaError(ProviderResponseError):
    pass


class DataUnavailableError(WavebirdError):
    exit_code = 8


class ValuationMathError(WavebirdError):
    exit_code = 9
