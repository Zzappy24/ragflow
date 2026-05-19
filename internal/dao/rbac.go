package dao

import (
	"errors"
	"ragflow/internal/common"

	"gorm.io/gorm"
)

// CUSTOM B2B SaaS — RBAC context resolver.
//
// Mirror of `_resolve_rbac_context` in `api/apps/extensions/rbac.py`. The
// Python version did a 4-statement reduction to a single peewee JOIN
// (User + Workspace + OrgMember + WsMember). We replicate the same single
// query here so HTTP authorization stays at ~5-8 ms per cold lookup, then
// cache the result on the gin.Context (request-scoped) the same way the
// Python implementation caches on `quart.g._rbac_ctx`.

// RBACContext is the resolved authorization context for a (user, tenant)
// pair. All fields default to their zero value when the underlying row is
// absent (e.g. the user has no workspace membership for that tenant) —
// mirrors the Python dict shape returned by `_resolve_rbac_context`.
type RBACContext struct {
	IsSuperuser      bool
	Email            string
	WorkspaceID      string // empty string when no workspace matches the tenant_id
	WorkspaceOrgID   string
	OrgRole          common.OrgRole // empty when not a member of the workspace's org
	WsRole           common.WsRole  // empty when not a workspace member
}

// rbacRow is the row shape returned by the JOIN query. Gorm fills fields by
// name; absent LEFT JOIN matches end up as the zero value (empty strings,
// false). Pointers are kept for IsSuperuser because the Python column is
// nullable and we want to distinguish "false" from "NULL".
type rbacRow struct {
	IsSuperuser    *bool  `gorm:"column:is_superuser"`
	Email          string `gorm:"column:email"`
	WorkspaceID    string `gorm:"column:workspace_id"`
	WorkspaceOrgID string `gorm:"column:workspace_org_id"`
	OrgRole        string `gorm:"column:org_role"`
	WsRole         string `gorm:"column:ws_role"`
}

// RBACDao exposes RBAC context resolution. It is stateless; instances are
// cheap and may be created per-request if convenient.
type RBACDao struct{}

func NewRBACDao() *RBACDao { return &RBACDao{} }

// ResolveContext returns the full RBAC context for (userID, tenantID).
// Returns (nil, nil) if the user does not exist — mirrors Python's
// `_resolve_rbac_context` returning `None`. Other errors propagate.
//
// The query is one LEFT JOIN chain:
//
//	user
//	  LEFT JOIN workspace ON workspace.tenant_id = ?
//	  LEFT JOIN org_member  ON org_member.org_id = workspace.org_id AND org_member.user_id = user.id
//	  LEFT JOIN ws_member   ON ws_member.workspace_id = workspace.id AND ws_member.user_id = user.id
//	WHERE user.id = ?
//
// Both `workspace_id` and the role fields may be empty in the result row;
// callers must treat empty as "no membership / no workspace".
func (d *RBACDao) ResolveContext(userID, tenantID string) (*RBACContext, error) {
	var row rbacRow
	err := DB.Table("user").
		Select(`user.is_superuser AS is_superuser,
		         user.email        AS email,
		         workspace.id      AS workspace_id,
		         workspace.org_id  AS workspace_org_id,
		         org_member.role   AS org_role,
		         ws_member.role    AS ws_role`).
		Joins("LEFT JOIN workspace  ON workspace.tenant_id = ?", tenantID).
		Joins("LEFT JOIN org_member ON org_member.org_id = workspace.org_id AND org_member.user_id = user.id").
		Joins("LEFT JOIN ws_member  ON ws_member.workspace_id = workspace.id AND ws_member.user_id = user.id").
		Where("user.id = ?", userID).
		Take(&row).Error
	if err != nil {
		// gorm returns ErrRecordNotFound for empty result. Convert to (nil, nil)
		// to match the Python contract — caller decides whether that's a deny.
		if isRecordNotFound(err) {
			return nil, nil
		}
		return nil, err
	}

	ctx := &RBACContext{
		Email:          row.Email,
		WorkspaceID:    row.WorkspaceID,
		WorkspaceOrgID: row.WorkspaceOrgID,
	}
	if row.IsSuperuser != nil {
		ctx.IsSuperuser = *row.IsSuperuser
	}
	// Only accept role strings the Python enum recognises. A corrupted row
	// (foreign role) is treated as "no role" → access denied, never granted
	// by accident.
	if common.IsValidOrgRole(row.OrgRole) {
		ctx.OrgRole = common.OrgRole(row.OrgRole)
	}
	if common.IsValidWsRole(row.WsRole) {
		ctx.WsRole = common.WsRole(row.WsRole)
	}
	return ctx, nil
}

// isRecordNotFound wraps gorm.ErrRecordNotFound detection.
func isRecordNotFound(err error) bool {
	return errors.Is(err, gorm.ErrRecordNotFound)
}
