All things community for [Open Source Europe (OSE)](https://opensourceeurope.org/).

## About

Open Source Europe is a European nonprofit that gives open source projects a shared fiscal and legal home. Instead of every project setting up its own legal entity, OSE provides a common nonprofit structure so maintainers can receive donations, manage expenses, and operate transparently — while staying fully autonomous.

This repository is the home for community-related discussions, governance, processes, and shared resources for projects under the OSE umbrella.

## Built with

The application process for new collectives is automated with
[n8n](https://n8n.io), self-hosted on a European VPS. The workflows that run it,
and the documentation for operating them, are in
[`automation/`](automation/). The first read of each application uses a model
hosted in the EU; it is advisory only, and every approval and rejection is made
by a person on Open Collective, per the
[AI policy](https://github.com/opensourceeurope/.github/blob/main/AI-POLICY.md).

## License

This repository carries two licences:

- Everything **except** `automation/` — governance, process, and other
  content — is licensed under [CC-BY-4.0](LICENSE).
- Everything under [`automation/`](automation/) is software and is licensed
  under the [MIT License](automation/LICENSE).

Contributions are accepted under the licence covering the path they touch.
