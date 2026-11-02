# Security

Keep Woffu tokens in local credential files with restricted permissions or environment variables. Do not include credentials or real employee data in issues, pull requests, screenshots or test fixtures. If a token is exposed, replace/revoke it through the account's available controls.

The optional MCP integration communicates over local stdio and exposes write tools only with `--allow-writes`. Tool results contain time records visible to the connected AI client. Review that client's permissions and data handling settings. The CLI and scheduler use Woffu's internal API; this project provides no shared hosting or account service.

## Reporting a vulnerability

Use [GitHub's private vulnerability reporting form](https://github.com/jamessi1989/woffu-helper/security/advisories/new). Include the affected version, impact and a minimal reproduction with synthetic data. Do not send a live Woffu token.

Security fixes target the latest release and `main`. This is a community project with no guaranteed response time.
