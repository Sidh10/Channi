"""Real-world figures the synthetic generator is calibrated to.

Only figures marked **Verified** in docs/data-sources.md may live in this module.
Every other number in this package is a SYNTHETIC parameter (see
`chhanni_audit.synthetic.SyntheticParams`) and must be described as such.
"""

# CRDT, IIT Delhi MCD SWM proposal (2026): municipal solid waste generated in
# Delhi, tonnes per day. docs/data-sources.md row 1, status: Verified.
# Used ONLY as the city-wide mean daily total of the synthetic ward log.
CRDT_DELHI_GENERATED_TPD: float = 11_862.0

CRDT_SOURCE_NOTE: str = (
    "11,862 TPD generated (CRDT, IIT Delhi MCD SWM proposal 2026; "
    "docs/data-sources.md row 1, Verified). The synthetic city's mean daily "
    "total is scaled to equal this figure; nothing else is calibrated to real data."
)
