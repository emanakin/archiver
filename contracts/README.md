# Contracts

`openapi.json` is generated from the scaffold's implemented FastAPI routes. It does not promise planned endpoints. Response dictionaries are intentionally minimal; add Pydantic response models before generating a strongly typed SDK.

Figma's standalone brief contains the richer target types and provider methods. Use an adapter during frontend integration, then evolve the API through explicit migrations/contracts. Run `scripts/export_openapi.py` with the backend environment and a development INTERNAL_API_TOKEN to refresh this file.
