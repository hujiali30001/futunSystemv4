import json
import smtplib
from email.message import EmailMessage
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from models import User


class UserNotifier:

    def __init__(self, db: Session) -> None:
        self.db = db

    def send_risk_alert(self, user: User, title: str, body_lines: list[str]) -> dict:
        result: dict = {"email": "skipped", "feishu": "skipped"}

        if user.smtp_config_json and user.email:
            smtp = user.smtp_config_json
            host = smtp.get("host", "")
            port = int(smtp.get("port", 465))
            username = smtp.get("username", "")
            password = smtp.get("password", "")
            if host and username and password:
                try:
                    msg = EmailMessage()
                    msg["From"] = username
                    msg["To"] = user.email
                    msg["Subject"] = f"[FuRun 风控告警] {title}"
                    msg.set_content("\n".join(body_lines))
                    with smtplib.SMTP_SSL(host, port, timeout=10) as server:
                        server.login(username, password)
                        server.send_message(msg)
                    result["email"] = "ok"
                except Exception as exc:
                    result["email"] = str(exc)[:200]

        if user.feishu_webhook_url:
            try:
                text = f"[FuRun 风控告警] {title}\n" + "\n".join(f"· {line}" for line in body_lines)
                payload = {"msg_type": "text", "content": {"text": text}}
                body = json.dumps(payload).encode("utf-8")
                req = Request(user.feishu_webhook_url, data=body, headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=5) as resp:
                    resp.read()
                result["feishu"] = "ok"
            except Exception as exc:
                result["feishu"] = str(exc)[:200]

        return result
