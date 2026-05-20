package common

import "testing"

// TestRoleMatrix locks in the role→permission matrix that mirrors
// `ROLE_PERMISSIONS` in api/apps/extensions/rbac.py. Any change to either
// side without updating the other will fail this test, and there is also
// a cross-language parity test in test/multitenant/test_go_rbac_parity.py
// that re-parses both files on CI to catch drift.
func TestRoleMatrix(t *testing.T) {
	tests := []struct {
		name string
		role WsRole
		perm Permission
		want bool
	}{
		// VIEWER — read-only on visible content, no admin actions.
		{"viewer can read datasets", WsRoleViewer, PermDatasetRead, true},
		{"viewer can read documents", WsRoleViewer, PermDocumentRead, true},
		{"viewer can read chats", WsRoleViewer, PermChatRead, true},
		{"viewer can use chats", WsRoleViewer, PermChatUse, true},
		{"viewer can read agents", WsRoleViewer, PermAgentRead, true},
		{"viewer CANNOT create dataset", WsRoleViewer, PermDatasetCreate, false},
		{"viewer CANNOT delete chat", WsRoleViewer, PermChatDelete, false},
		{"viewer CANNOT configure LLM", WsRoleViewer, PermLLMConfigure, false},
		{"viewer CANNOT manage API keys", WsRoleViewer, PermAPIKeyManage, false},
		{"viewer CANNOT invite members", WsRoleViewer, PermMemberInvite, false},

		// EDITOR — all dataset/document/chat/agent CRUD; no admin permissions.
		{"editor can create dataset", WsRoleEditor, PermDatasetCreate, true},
		{"editor can update dataset", WsRoleEditor, PermDatasetUpdate, true},
		{"editor can delete dataset", WsRoleEditor, PermDatasetDelete, true},
		{"editor can delete chat", WsRoleEditor, PermChatDelete, true},
		{"editor can update agent", WsRoleEditor, PermAgentUpdate, true},
		{"editor CANNOT configure LLM", WsRoleEditor, PermLLMConfigure, false},
		{"editor CANNOT configure datasource", WsRoleEditor, PermDatasourceConfigure, false},
		{"editor CANNOT manage API keys", WsRoleEditor, PermAPIKeyManage, false},
		{"editor CANNOT invite members", WsRoleEditor, PermMemberInvite, false},
		{"editor CANNOT manage groups", WsRoleEditor, PermGroupManage, false},
		{"editor CANNOT read audit", WsRoleEditor, PermAuditRead, false},

		// WS_ADMIN — Python source says `WsRole.WS_ADMIN: set(Permission)`
		// (every permission granted). We assert this by spot-checking the
		// admin-only permissions plus a sampling of regular ones.
		{"ws_admin can configure LLM", WsRoleWsAdmin, PermLLMConfigure, true},
		{"ws_admin can configure datasource", WsRoleWsAdmin, PermDatasourceConfigure, true},
		{"ws_admin can configure MCP", WsRoleWsAdmin, PermMCPConfigure, true},
		{"ws_admin can manage API keys", WsRoleWsAdmin, PermAPIKeyManage, true},
		{"ws_admin can invite members", WsRoleWsAdmin, PermMemberInvite, true},
		{"ws_admin can remove members", WsRoleWsAdmin, PermMemberRemove, true},
		{"ws_admin can read audit", WsRoleWsAdmin, PermAuditRead, true},
		{"ws_admin can manage groups", WsRoleWsAdmin, PermGroupManage, true},
		{"ws_admin can create dataset", WsRoleWsAdmin, PermDatasetCreate, true},
		{"ws_admin can use chat", WsRoleWsAdmin, PermChatUse, true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := HasRolePermission(tc.role, tc.perm)
			if got != tc.want {
				t.Fatalf("HasRolePermission(%s, %s) = %v, want %v",
					tc.role, tc.perm, got, tc.want)
			}
		})
	}
}

// TestUnknownRoleDeniesEverything confirms that a corrupted or unknown
// ws_role string never accidentally grants a permission. The Python side
// returns False via `ROLE_PERMISSIONS.get(ws_role, set())`; Go returns
// false via the matrix lookup. We test the empty WsRole and a bogus value.
func TestUnknownRoleDeniesEverything(t *testing.T) {
	cases := []WsRole{"", WsRole("not_a_role"), WsRole("admin")} // "admin" != "ws_admin"
	perms := []Permission{
		PermDatasetRead, PermChatUse, PermAgentRead,
		PermLLMConfigure, PermAPIKeyManage,
	}
	for _, role := range cases {
		for _, p := range perms {
			if HasRolePermission(role, p) {
				t.Fatalf("HasRolePermission(%q, %q) = true, want false (unknown role must deny)", role, p)
			}
		}
	}
}

func TestRoleValidators(t *testing.T) {
	for _, r := range []string{"viewer", "editor", "ws_admin"} {
		if !IsValidWsRole(r) {
			t.Errorf("IsValidWsRole(%q) = false, want true", r)
		}
	}
	for _, r := range []string{"", "admin", "owner", "VIEWER"} {
		if IsValidWsRole(r) {
			t.Errorf("IsValidWsRole(%q) = true, want false", r)
		}
	}
	for _, r := range []string{"member", "org_admin"} {
		if !IsValidOrgRole(r) {
			t.Errorf("IsValidOrgRole(%q) = false, want true", r)
		}
	}
	for _, r := range []string{"", "viewer", "admin"} {
		if IsValidOrgRole(r) {
			t.Errorf("IsValidOrgRole(%q) = true, want false", r)
		}
	}
}
