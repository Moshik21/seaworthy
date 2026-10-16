"""Seaworthy: a release-readiness check engine.

Standard library only. The command line is `bin/seaworthy`; this package is its engine.
"""
VERSION = "0.1.2"
PROFILE_SCHEMA = ("seaworthy.profile", "1.0")
REPORT_SCHEMA = ("seaworthy.report", "1.0")
RECEIPT_SCHEMA = ("seaworthy.receipt", "1.0")
LEDGER_SCHEMA = ("seaworthy.ledger", "1.0")
FIXTURES_SCHEMA = ("seaworthy.fixtures", "1.0")
IMAGES_SCHEMA = ("seaworthy.images", "1.0")

# Engine exit codes (the same numbers Replicated Troubleshoot's pre-flight checks use).
EXIT_PASS, EXIT_ERROR, EXIT_BAD_SPEC, EXIT_NO_GO, EXIT_WARN = 0, 1, 2, 3, 4
