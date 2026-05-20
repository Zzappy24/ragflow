package common

// CUSTOM B2B SaaS — RBAC permissions enum.
//
// Mirror of `api/apps/extensions/rbac.py` (Permission, WsRole, OrgRole,
// ROLE_PERMISSIONS). The Python module remains the source of truth for the
// admin UI; this file is a frozen copy consumed by the Go façade so that
// HTTP authorization decisions are taken locally, without a cross-process
// hop into Python on every request.
//
// Maintenance rule: if you add or rename a permission in rbac.py, mirror
// the change here in the same PR. The integration test
// `test/multitenant/test_go_rbac_parity.py` (added in the same migration)
// re-parses both files and fails CI on drift.

// Permission represents a fine-grained action a user can perform within a
// workspace. Values match the Python enum strings exactly.
type Permission string

const (
	// Datasets (knowledge bases).
	PermDatasetCreate Permission = "dataset.create"
	PermDatasetRead   Permission = "dataset.read"
	PermDatasetUpdate Permission = "dataset.update"
	PermDatasetDelete Permission = "dataset.delete"

	// Documents within a dataset.
	PermDocumentCreate Permission = "document.create"
	PermDocumentRead   Permission = "document.read"
	PermDocumentDelete Permission = "document.delete"

	// Chat assistants and sessions.
	PermChatCreate Permission = "chat.create"
	PermChatRead   Permission = "chat.read"
	PermChatUpdate Permission = "chat.update"
	PermChatDelete Permission = "chat.delete"
	PermChatUse    Permission = "chat.use"

	// Agents (canvases).
	PermAgentCreate Permission = "agent.create"
	PermAgentRead   Permission = "agent.read"
	PermAgentUpdate Permission = "agent.update"
	PermAgentDelete Permission = "agent.delete"

	// Workspace membership management.
	PermMemberInvite Permission = "member.invite"
	PermMemberRemove Permission = "member.remove"
	PermMemberList   Permission = "member.list"

	// Group / role administration.
	PermGroupManage Permission = "group.manage"
	PermAuditRead   Permission = "audit.read"

	// CUSTOM B2B SaaS — see CLAUDE.md "Custom B2B SaaS Multi-Tenant Layer".
	PermLLMConfigure        Permission = "llm.configure"
	PermDatasourceConfigure Permission = "datasource.configure"
	PermMCPConfigure        Permission = "mcp.configure"
	PermAPIKeyManage        Permission = "api_key.manage"
)

// WsRole is the per-workspace role assigned in `ws_member.role`.
type WsRole string

const (
	WsRoleViewer  WsRole = "viewer"
	WsRoleEditor  WsRole = "editor"
	WsRoleWsAdmin WsRole = "ws_admin"
)

// OrgRole is the per-organisation role assigned in `org_member.role`.
type OrgRole string

const (
	OrgRoleMember   OrgRole = "member"
	OrgRoleOrgAdmin OrgRole = "org_admin"
)

// rolePermissions mirrors `ROLE_PERMISSIONS` in rbac.py exactly.
//
// WS_ADMIN is intentionally NOT enumerated here — it is granted every
// permission via `HasRolePermission` returning true unconditionally when the
// role is ws_admin. This avoids drift between the Go set and the Python
// `set(Permission)` expression.
var rolePermissions = map[WsRole]map[Permission]struct{}{
	WsRoleViewer: {
		PermDatasetRead:  {},
		PermDocumentRead: {},
		PermChatRead:     {},
		PermChatUse:      {},
		PermAgentRead:    {},
	},
	WsRoleEditor: {
		PermDatasetCreate:  {},
		PermDatasetRead:    {},
		PermDatasetUpdate:  {},
		PermDatasetDelete:  {},
		PermDocumentCreate: {},
		PermDocumentRead:   {},
		PermDocumentDelete: {},
		PermChatCreate:     {},
		PermChatRead:       {},
		PermChatUpdate:     {},
		PermChatDelete:     {},
		PermChatUse:        {},
		PermAgentCreate:    {},
		PermAgentRead:      {},
		PermAgentUpdate:    {},
		PermAgentDelete:    {},
	},
}

// HasRolePermission returns true if a member with `role` is granted `perm`
// in the workspace. ws_admin is always true (mirrors Python's
// `WsRole.WS_ADMIN: set(Permission)`).
//
// This function does NOT consider org_admin or superuser bypasses — those
// are handled in middleware.RequirePermission via the full RBAC context.
func HasRolePermission(role WsRole, perm Permission) bool {
	if role == WsRoleWsAdmin {
		return true
	}
	perms, ok := rolePermissions[role]
	if !ok {
		return false
	}
	_, granted := perms[perm]
	return granted
}

// IsValidWsRole reports whether the given string is a known ws_member.role
// value. Used at the DB boundary to catch corrupted rows.
func IsValidWsRole(role string) bool {
	switch WsRole(role) {
	case WsRoleViewer, WsRoleEditor, WsRoleWsAdmin:
		return true
	}
	return false
}

// IsValidOrgRole reports whether the given string is a known org_member.role.
func IsValidOrgRole(role string) bool {
	switch OrgRole(role) {
	case OrgRoleMember, OrgRoleOrgAdmin:
		return true
	}
	return false
}
