"""Declarative configuration for the IPFT pipeline.

The point of this package is that a parameter is DECLARED ONCE — its family, type,
unit, description and whether it is a user input at all — and everything else is
derived from that declaration: the YAML loader validates against it, `describe`
prints from it, the HTML report's glossary is built from it, and the user guide is
generated from it. A manual written separately drifts; a manual generated from the
schema cannot.

    schema    what a parameter IS      (families, types, descriptions)
    loader    how a YAML file is READ  (entries with value/note/source, interpolation)
    resolve   how the pieces COMBINE   (city + line -> ScenarioPreset)
"""
