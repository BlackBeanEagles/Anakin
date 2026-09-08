"""Everything needed to put the intake link in front of a real person.

The bottleneck on this project is not code, it is cases. A grievance agent with no
grievances is a demo; the thing that cannot be copied by someone starting on the last
day is a week of real outcomes. So the sourcing tools get the same care as the
escalation ladder.

Three things live here:

  a QR code       generated on the fly, so the link works when it is a poster on a
                  noticeboard or a phone screen held up to another phone
  the outreach    copy that is honest about what this is - experimental, free, and
                  public - because a case sourced by overpromising is worse than no
                  case at all
  an OG card      an HTML page sized to screenshot into static/og.png, since a link
                  with no preview gets scrolled past in exactly the WhatsApp groups
                  where these cases live

Nothing here is public-facing except the QR itself: /share sits behind the console
password, because it is a tool for the operator, not a page for a citizen.
"""
import io

import segno

# 1200x630 is what every platform crops to. The card is rendered as HTML rather than
# drawn, so it restyles with the rest of the site instead of drifting out of date.
OG_WIDTH, OG_HEIGHT = 1200, 630


def qr_svg(url: str, *, scale: int = 6) -> str:
    """An SVG QR for a URL. SVG so it stays sharp printed on a noticeboard."""
    # segno's SVG writer emits bytes, not text, whatever the buffer is called.
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=scale, border=2,
                                    dark="#1a1a1a", light=None, xmldecl=False)
    return buf.getvalue().decode("utf-8")


# The copy. Written to be forwarded by a stranger, which means short lines, no jargon,
# and the catch stated up front rather than buried - anyone who feels misled after
# handing over a real complaint is a person this project has harmed, not a data point.

WHATSAPP = """*Has a government complaint of yours been ignored?*

I built something that might help, and it's free.

You send it what happened, in your own words — Hindi or English, doesn't matter. \
It writes a proper CPGRAMS grievance, files it, and then actually watches it. \
Every time a deadline passes with no reply, it escalates: nodal officer, \
appellate authority, up the chain. It doesn't get tired and it doesn't forget.

What I need to be straight about:
• It's new. I'm testing it. It might not work for you.
• It's free. No payment, ever, and I don't want your money.
• I never ask for your portal password. You do the one submit click yourself.
• Cases are published without your name, so others can see which departments \
actually answer. You can opt out and it still works.

If something of yours has been stuck — a parcel, a pension, a refund, a road — \
send it here: {url}

Or just forward this to someone it's been happening to."""


REDDIT = """**I built a free agent that files and escalates CPGRAMS grievances. \
Looking for real cases to test it on.**

Most complaints die because people file once and give up. The department knows that. \
The system is patient and we aren't.

So I built something that is. You describe the problem in plain words. It extracts \
the facts and reference numbers, routes it to the right ministry and category, drafts \
the grievance, and files it. Then it watches. When a statutory deadline passes with no \
real reply, it escalates automatically — nodal officer, then appellate authority, then \
higher — and it cites the actual dates back at them.

Being upfront:

- **It's free and always will be.** I'm not selling anything and there's no signup wall.
- **It's experimental.** Built this month. It may not help you.
- **I never see your portal password.** It prepares everything; you click submit. That \
boundary is deliberate.
- **Every case is published anonymously** — the point is a public record of which \
departments actually respond. Opt out and it still works for you.
- **It won't touch** anything police-related, court matters, or RTI. Those need a human.

If you've got something genuinely stuck — undelivered speed post, a pension that \
stopped, a refund that never came, a civic complaint nobody acted on — I'd like to \
try it: {url}

Happy to answer anything about how it works. Code is public."""


TWITTER = """Government complaint ignored?

I built a free agent that files your CPGRAMS grievance and then keeps escalating \
every time a deadline passes. Nodal officer → appellate authority → up.

It doesn't get tired. Departments count on you getting tired.

Experimental, free, no password needed: {url}"""


SHORT = "Free agent that files your govt complaint and escalates it until someone " \
        "answers. Experimental, no password needed: {url}"


# Where to actually post it. Ordered by how likely a real, stuck case is to be sitting
# there right now rather than by audience size.
CHANNELS = [
    ("Family and neighbourhood WhatsApp groups", WHATSAPP,
     "Highest hit rate by far. Someone in every group has a pension, parcel or "
     "refund that has been stuck for months. Send it to people who know you."),
    ("Your RWA or apartment group", WHATSAPP,
     "Civic complaints - roads, water, garbage, streetlights - and they are already "
     "annoyed enough to act."),
    ("r/india, r/bangalore, r/mumbai, r/legaladviceindia", REDDIT,
     "Read each subreddit's self-promotion rule first. Frame it as asking for test "
     "cases, which is true, and answer every comment."),
    ("Twitter/X, replying to complaint threads", TWITTER,
     "Search 'CPGRAMS' or 'no response from' and reply to people already describing "
     "a stuck grievance. Do not spam - reply where it genuinely fits."),
    ("College and alumni groups", SHORT,
     "Scholarship delays, certificate and transcript problems, hostel refunds."),
    ("A printed QR on a noticeboard", SHORT,
     "Society gate, college notice board, local shop. The QR below prints fine."),
]
