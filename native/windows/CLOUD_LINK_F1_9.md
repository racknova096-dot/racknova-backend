# RackNova Native F1.9 - Cloud Link

Native F1.9 must keep Local-First operation while preserving or explicitly activating the RackNova Cloud link.

The previous Native bootstrap created `config.json` with `cloud_url` empty and `secrets.dat` with an empty `node_credential`. That left `RACKNOVA_SYNC_AUTOSTART=false`, so a locally healthy install could have zero Local <-> Cloud communication.

The installer now:

- preserves an existing Cloud URL + node credential before repair/reinstall;
- restores that protected link after the local runtime is configured;
- offers the Cloud connection step during setup when a new link is required;
- passes the sync secret to the child process through a temporary inherited environment variable rather than the setup command line;
- validates the node with `/sync/v1/nodes/register` before persisting the Cloud link.

The Local runtime remains usable offline. Cloud synchronization starts only when a complete Cloud link is present.
