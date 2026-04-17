package dao

// WorkspaceDAO provides read-only access to the workspace and membership
// tables that are managed by the Python multi-tenant layer.
// We never write from Go — ownership of these tables stays in Python.

// Workspace mirrors the `workspace` table (Python: api/db/db_models.py:Workspace).
type Workspace struct {
	ID       string `gorm:"column:id;primaryKey;size:32"`
	OrgID    string `gorm:"column:org_id;size:32"`
	TenantID string `gorm:"column:tenant_id;size:32"`
	Status   string `gorm:"column:status;size:1"`
}

func (Workspace) TableName() string { return "workspace" }

// WsMember mirrors the `ws_member` table (Python: api/db/db_models.py:WsMember).
type WsMember struct {
	ID          string `gorm:"column:id;primaryKey;size:32"`
	WorkspaceID string `gorm:"column:workspace_id;size:32"`
	UserID      string `gorm:"column:user_id;size:32"`
	Role        string `gorm:"column:role;size:16"`
	Status      string `gorm:"column:status;size:1"`
}

func (WsMember) TableName() string { return "ws_member" }

// OrgMember mirrors the `org_member` table (Python: api/db/db_models.py:OrgMember).
type OrgMember struct {
	ID     string `gorm:"column:id;primaryKey;size:32"`
	OrgID  string `gorm:"column:org_id;size:32"`
	UserID string `gorm:"column:user_id;size:32"`
	Role   string `gorm:"column:role;size:16"`
	Status string `gorm:"column:status;size:1"`
}

func (OrgMember) TableName() string { return "org_member" }

// WorkspaceDAO workspace data access object
type WorkspaceDAO struct{}

func NewWorkspaceDAO() *WorkspaceDAO { return &WorkspaceDAO{} }

// GetActiveByID returns the workspace only if it exists and is active (status=1).
func (d *WorkspaceDAO) GetActiveByID(workspaceID string) (*Workspace, error) {
	var ws Workspace
	err := DB.Where("id = ? AND status = ?", workspaceID, "1").First(&ws).Error
	if err != nil {
		return nil, err
	}
	return &ws, nil
}

// IsMember returns true when userID has an active WsMember row for workspaceID.
func (d *WorkspaceDAO) IsMember(workspaceID, userID string) bool {
	var count int64
	DB.Model(&WsMember{}).
		Where("workspace_id = ? AND user_id = ? AND status = ?", workspaceID, userID, "1").
		Count(&count)
	return count > 0
}

// IsOrgAdmin returns true when userID has role=org_admin in the org that owns workspaceID.
func (d *WorkspaceDAO) IsOrgAdmin(orgID, userID string) bool {
	var count int64
	DB.Model(&OrgMember{}).
		Where("org_id = ? AND user_id = ? AND role = ? AND status = ?", orgID, userID, "org_admin", "1").
		Count(&count)
	return count > 0
}
