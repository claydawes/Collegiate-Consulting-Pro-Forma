"""Collegiate Consulting visual identity for the app.

Sampled from the live collegiateconsulting.com, not matched by eye. An
earlier version of this module recorded that the site's stylesheet couldn't
be read and that its values were inferred from the logo files instead. Read
properly, the site turned out to be quite different from what was inferred,
and every one of these was wrong:

    inferred                        actual
    ------------------------------  --------------------------------------
    "no accent colour in the        a strong yellow used for headings,
    chrome - emphasis comes from    filled buttons and hero type
    weight and scale rather than
    hue"
    headings set bold *lowercase*   headings are UPPERCASE, weight 600-900
    Barlow Condensed + Inter        Montserrat throughout - 380 of 380
                                    sampled elements, no second face
    accent #FFC000                  #FFE739 (headings, filled buttons) and
                                    #FFD00F (outlined buttons, links)

The palette below is what the site actually computes to. Colours that also
appear inside the workbooks are noted, since those are set independently in
pfa/export/style.py and the two are not wired together - the workbooks'
gold came from the client's own Excel reference, not from the website.

Anything Streamlit's theme system can express now lives in
.streamlit/config.toml instead of here. What's left in this module is the
part the theme system has no setting for: the uppercase headings, the two
button treatments, and the small layout pieces the app draws itself.
"""

from __future__ import annotations

#: Logo files, served from their own CDN so the app always shows whatever is
#: live on the site. Both verified reachable.
LOGO_URL = "https://collegiateconsulting.com/wp-content/uploads/2023/03/CClogo_onwhite-2-scaled.png"
LOGO_SQUARE_URL = "https://collegiateconsulting.com/wp-content/uploads/2023/03/CC_DES_LOGO_BLACK-300x300.png"
SITE_URL = "https://collegiateconsulting.com"

# --- Sampled palette ---------------------------------------------------
INK = "#000000"        # body copy and headings on light ground
CHARCOAL = "#2C2C2C"   # the hero band and dark section backgrounds
YELLOW = "#FFE739"     # hero headings, filled buttons
GOLD = "#FFD00F"       # outlined buttons, accents; the deeper of the two
OFFWHITE = "#F5F5F5"   # page background
GREY = "#808184"       # button borders on dark ground
MUTED = "#6B6B6B"      # secondary copy
LINE = "#E2E2E2"       # hairlines

CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;900&display=swap');

  /* The @import above downloads Montserrat; .streamlit/config.toml names it
     as theme.font, which is what actually applies it - including to the
     headings, where a hand-written `h1 {{ font-family }}` rule loses to
     Streamlit's own styles. Both halves are needed: naming it without
     loading it silently falls back to the default face, and loading it
     without naming it leaves the headings untouched. */

  .block-container {{ padding-top: 2.25rem; max-width: 920px; }}

  /* Uppercase headings are the site's most recognisable typographic move.
     Weights come from the theme; only the casing needs saying here. */
  h1, h2, h3, h4 {{
      text-transform: uppercase;
      color: {INK};
  }}
  h1 {{ line-height: 1.08; }}

  /* The site marks every section with a yellow rule. Carrying it onto the
     step headings keeps the brand present past the sign-in screen, which
     otherwise runs entirely black-on-white.

     Scoped to stHeading - the container st.header/st.title render into.
     Not stMarkdown, which is a different wrapper and doesn't contain these. */
  [data-testid="stHeading"] h1::after,
  [data-testid="stHeading"] h2::after {{
      content: "";
      display: block;
      width: 3.5rem;
      border-top: 4px solid {GOLD};
      margin-top: .6rem;
  }}

  /* Eyebrow + rule, echoing the site's section dividers. The rule is the
     brand yellow rather than black - on the site the yellow is what marks a
     section, and black is just body copy. */
  .cc-eyebrow {{
      text-transform: uppercase;
      letter-spacing: .18em;
      font-size: .72rem;
      font-weight: 700;
      color: {MUTED};
      margin-bottom: .35rem;
  }}
  .cc-rule {{ border-top: 4px solid {GOLD}; margin: .2rem 0 1.5rem 0; }}

  /* Buttons follow the site's two treatments.
     Primary  = filled yellow, black text, uppercase 700 - the site's
                "FEASIBILITY STUDY" call to action.
     Secondary = outlined, black text on white.
     The black text on primary has to be forced: Streamlit picks a contrasting
     text colour for the primary button itself, and on this yellow it chooses
     white, which is close to illegible. */
  /* Matched on the button's own `kind` attribute rather than on its
     container's class. Streamlit puts form-submit buttons in a different
     wrapper (stFormSubmitButton, kind="primaryFormSubmit") from ordinary
     ones (stButton, kind="primary"), so a container-based rule styles some
     buttons and silently misses others - which left the sign-in button
     rendering Streamlit's default white-on-yellow. */
  button[kind="primary"], button[kind="primaryFormSubmit"],
  button[kind="secondary"], button[kind="secondaryFormSubmit"],
  [data-testid="stDownloadButton"] button {{
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .04em;
      font-size: .8rem;
      padding: .55rem 1.15rem;
  }}

  /* Filled yellow with black text - the site's own call-to-action. The black
     has to be forced: Streamlit picks the text colour for a primary button
     itself and chooses white, which on this yellow is close to invisible. */
  button[kind="primary"], button[kind="primaryFormSubmit"],
  [data-testid="stDownloadButton"] button {{
      background: {YELLOW};
      color: {INK} !important;
      border: 1px solid {INK};
  }}
  button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover,
  [data-testid="stDownloadButton"] button:hover {{
      background: {GOLD};
      color: {INK} !important;
      border-color: {INK};
  }}

  button[kind="secondary"], button[kind="secondaryFormSubmit"] {{
      background: #FFFFFF;
      color: {INK} !important;
      border: 1px solid {GREY};
  }}
  button[kind="secondary"]:hover, button[kind="secondaryFormSubmit"]:hover {{
      border-color: {INK};
      color: {INK} !important;
  }}

  button:disabled, button:disabled:hover {{
      background: {LINE} !important;
      border-color: {LINE} !important;
      color: {MUTED} !important;
  }}

  /* Upload target: dashed, square, quiet until used. */
  [data-testid="stFileUploaderDropzone"] {{
      border: 2px dashed {LINE};
      background: {OFFWHITE};
  }}

  [data-testid="stSidebar"] {{
      background: {OFFWHITE};
      border-right: 1px solid {LINE};
  }}
  /* The signed-in address is longer than the sidebar is wide and was being
     broken mid-word ("...collegiateconsultin / g.com"). */
  [data-testid="stSidebar"] a {{
      overflow-wrap: anywhere;
      font-size: .82rem;
  }}

  /* Step tracker in the sidebar. */
  .cc-step {{ font-size: .9rem; padding: .15rem 0; color: {MUTED}; }}
  .cc-step-active {{ color: {INK}; font-weight: 700; }}
  .cc-step-done {{ color: {INK}; }}

  .cc-footer {{
      color: {MUTED};
      font-size: .76rem;
      border-top: 1px solid {LINE};
      margin-top: 2.5rem;
      padding-top: .75rem;
  }}
</style>
"""


def header_html(subtitle: str) -> str:
    return (
        f'<div class="cc-eyebrow">{subtitle}</div>'
        f'<div class="cc-rule"></div>'
    )


def footer_html() -> str:
    return (
        f'<div class="cc-footer">Collegiate Consulting — internal tool. '
        f'<a href="{SITE_URL}" target="_blank" style="color:{MUTED};">collegiateconsulting.com</a>'
        f"</div>"
    )
