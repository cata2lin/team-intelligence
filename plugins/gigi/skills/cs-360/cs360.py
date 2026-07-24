"""cs360.py — profil 360° CS, UN singur skill. Rulează scriptul potrivit din același director:
  customer      -> cs_customer_360.py       (--phone/--email/--name -> comenzi, LTV, refuzuri, flag REFUZNIC SERIAL)
  conversation  -> cs_profile.py            (profil conversatie SCRIPTAT zero-LLM; --llm -> cs_conversation_profile.py)
  order|wismo   -> cs_order_status.py       (--order/--awb/--phone/--email -> WISMO + tracking live; --reply)

Scripturile stau IN cs-360/ (aceeasi adancime ca un skill dir), deci rezolva rp_db (richpanel-export) + kb prin __file__.
  uv run cs360.py customer --phone 07...
  uv run cs360.py conversation --conv <id> [--llm]
  uv run cs360.py wismo --order GT123 --reply
"""
import sys, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = {"customer": "cs_customer_360.py", "order": "cs_order_status.py", "wismo": "cs_order_status.py"}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    mode, args = sys.argv[1], sys.argv[2:]
    if mode == "conversation":
        if "--llm" in args:
            args.remove("--llm"); script = "cs_conversation_profile.py"
        else:
            script = "cs_profile.py"
    elif mode in TARGETS:
        script = TARGETS[mode]
    else:
        sys.exit("mod necunoscut '%s' — foloseste: customer | conversation | order | wismo" % mode)
    sys.exit(subprocess.run(["uv", "run", os.path.join(HERE, script), *args]).returncode)


if __name__ == "__main__":
    main()
