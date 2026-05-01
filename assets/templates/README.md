# DOCX templates for `RenderDocxTemplate` agent tool

These are workspace-uploadable assets that pair with `agent/tools/render_docx_template.py`.

## `cyllene_procedure.docx`

Cyllene-branded procedure template (FICHE DE PROCEDURE) pre-templated with
Jinja placeholders. Generated from `_source_cyllene_procedure.docx` by
`scripts/build_cyllene_template.py`.

**JSON schema the LLM must produce** when calling the `render_docx_template`
tool with this template:

```json
{
  "procedure_name": "Embauche fiche S3",
  "beneficiaires": [
    "DRH",
    "Manager hiérarchique",
    "Responsable sécurité"
  ],
  "finalite": "Sécuriser le processus d'habilitation défense pour les collaborateurs accédant à des informations classifiées.",
  "revision_date": "01/05/2026",
  "revision_number": "V1",
  "revision_auteur": "Yoan SAPIENZA",
  "revision_approbateur": "PDG",
  "revision_nature": "Création",
  "sections": [
    {
      "titre": "VERIFICATIONS PREALABLES",
      "intro": "Avant de lancer la demande d'habilitation :",
      "puces": [
        "Vérifier l'identité du collaborateur",
        "Confirmer que le poste est bien soumis à fiche S3",
        "S'assurer que le casier judiciaire est vierge"
      ]
    },
    {
      "titre": "CONSTITUTION DU DOSSIER",
      "intro": "Le dossier comprend :",
      "puces": [
        "Formulaire 94A complété",
        "Justificatifs d'identité"
      ]
    }
  ]
}
```

### Field semantics

| Field | Meaning |
|-------|---------|
| `procedure_name` | Title rendered in the header (kept as authored, no `.upper()` applied — the Cyllene style sheet handles the visual casing). |
| `beneficiaires` | Recipients of the procedure (rendered as bulleted list). |
| `finalite` | Single-sentence purpose statement (rendered as a single bullet). |
| `revision_*` | Single-revision metadata for the "SUIVI DES MODIFICATIONS" table. The current template only supports one revision row — see "Limitations". |
| `sections` | Body of the procedure. Each section is numbered automatically (`{{ loop.index }}`) and rendered with: a `Heading 1` title, an intro paragraph, a bulleted list of `puces`. |

## Why a single revision row, not a list?

Initial design used `{%tr for r in revisions %}` to loop over multiple
revisions. docxtpl 0.20's row-level regex matched the LAST `{%tr ... %}`
in a row greedily and erased the row's contents before Jinja saw them,
producing an orphan `{% endfor %}` that crashed compilation. The clean
fix is dedicated wrap-rows above and below the data row, but those
appear as visible empty rows when the template is opened in Word — UX
unfriendly.

Pragmatic choice: for a freshly-generated procedure (V1, Création), one
revision entry is enough. If a future use-case needs an N-revision
history, add wrap-rows around the data row in
`scripts/build_cyllene_template.py` and migrate the schema.

## Regenerating the templated `.docx`

If you edit `_source_cyllene_procedure.docx` (e.g. updating the Cyllene
branding or table structure), regenerate the Jinja-templated version:

```bash
PATH=/Users/zappy/.local/bin:$PATH PYTHONPATH=$(pwd) \
    uv run python scripts/build_cyllene_template.py \
    assets/templates/cyllene_procedure.docx
```

The script transforms placeholder content (`Nom de la Procedure`,
`Blabla`, `XX`, `YY`, `Création`, etc.) into the Jinja directives the
tool expects, while preserving fonts, colors, table widths, header
image, and footer page numbering.
