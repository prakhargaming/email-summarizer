import argparse
import json
import logging
import sys

from sqlalchemy import or_, select

from app.db import SessionLocal
from app.db.models import GmailCredential, User
from app.pipeline.digest import run_for_user


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    q = (select(User.id).join(GmailCredential, GmailCredential.user_id == User.id)
         .where(or_(GmailCredential.needs_reauth.is_(False), GmailCredential.needs_reauth.is_(None))))
    if args.email:
        q = q.where(User.email == args.email.strip().lower())
    with SessionLocal() as db:
        user_ids = db.scalars(q).all()

    failed = 0
    for uid in user_ids:
        try:
            with SessionLocal() as db:
                failed += run_for_user(db, uid).status == "failed"
        except Exception as e:  # e.g. the DB dropped mid-run; keep going for the other users
            logging.info(json.dumps({"event": "run_crashed", "user_id": str(uid), "error_type": type(e).__name__}))
            failed += 1
    logging.info(json.dumps({"event": "job_finished", "users": len(user_ids), "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
