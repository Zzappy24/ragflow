#!/usr/bin/env python3
"""Répare les défauts de modèles vides des tenants de workspaces.

Contexte (incident 2026-09-01, « no default embedding ») : l'héritage du
workspace template (settings_json.model_template) est une PHOTOCOPIE à la
création du workspace — jamais re-synchronisée. Tout workspace créé pendant
une fenêtre où le template avait des défauts vides a hérité du vide, pour
toujours, et le parsing/chat y échoue avec un message peu parlant.

Ce script copie les défauts du tenant template vers les tenants de
workspaces dont le champ correspondant est VIDE. Il ne touche jamais un
défaut déjà posé (pas d'écrasement), et ne touche pas les tenants
personnels (uniquement les tenants référencés par un workspace actif).

Usage (depuis un pod api ou un environnement avec accès DB) :
    python tools/scripts/fix_empty_workspace_defaults.py            # dry-run
    python tools/scripts/fix_empty_workspace_defaults.py --execute  # applique
"""
import argparse
import sys

from api.db.db_models import DB, Tenant, Workspace

DEFAULT_FIELDS = ("llm_id", "embd_id", "asr_id", "img2txt_id", "rerank_id", "tts_id")


def find_template_tenant():
    for w in Workspace.select().where(Workspace.status == "1"):
        if (w.settings_json or {}).get("model_template") is True:
            return w
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="applique les changements (défaut : dry-run)")
    args = parser.parse_args()

    with DB.connection_context():
        template_ws = find_template_tenant()
        if not template_ws:
            print("ERREUR : aucun workspace template (settings_json.model_template) trouvé.")
            sys.exit(1)

        template = Tenant.get_or_none(Tenant.id == template_ws.tenant_id)
        if not template:
            print(f"ERREUR : tenant template {template_ws.tenant_id} introuvable.")
            sys.exit(1)

        print(f"Template : workspace « {template_ws.name} » (tenant {template.id})")
        for f in DEFAULT_FIELDS:
            print(f"  {f:12s} = {getattr(template, f, '') or '(vide)'}")

        empty_template_fields = [f for f in DEFAULT_FIELDS if not (getattr(template, f, "") or "")]
        if "embd_id" in empty_template_fields or "llm_id" in empty_template_fields:
            print("\nATTENTION : le template lui-même n'a pas d'embd_id/llm_id — "
                  "pose d'abord ses défauts dans le panel admin, puis relance.")
            sys.exit(1)

        fixed, clean = 0, 0
        for ws in Workspace.select().where(Workspace.status == "1"):
            if ws.id == template_ws.id:
                continue
            tenant = Tenant.get_or_none(Tenant.id == ws.tenant_id)
            if not tenant:
                print(f"- {ws.name}: tenant {ws.tenant_id} introuvable, ignoré")
                continue
            updates = {}
            for f in DEFAULT_FIELDS:
                if not (getattr(tenant, f, "") or "") and (getattr(template, f, "") or ""):
                    updates[f] = getattr(template, f)
            if not updates:
                clean += 1
                continue
            fixed += 1
            detail = ", ".join(f"{k} ← {v}" for k, v in updates.items())
            if args.execute:
                Tenant.update(**updates).where(Tenant.id == tenant.id).execute()
                print(f"- {ws.name}: CORRIGÉ ({detail})")
            else:
                print(f"- {ws.name}: à corriger ({detail})")

        mode = "appliqué" if args.execute else "dry-run — rien n'a été modifié (ajoute --execute)"
        print(f"\n{fixed} workspace(s) corrigé(s), {clean} déjà complet(s). Mode : {mode}")


if __name__ == "__main__":
    main()
