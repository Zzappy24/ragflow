// CUSTOM B2B SaaS — model configuration is managed exclusively via the admin panel.
// See CLAUDE.md "Custom B2B SaaS Multi-Tenant Layer" for merge warnings.
// Original ModelProviders component replaced to prevent per-user model changes.

const ModelProviders = () => {
  return (
    <div className="flex items-center justify-center h-64 w-full border-[0.5px] border-border-button rounded-lg">
      <div className="text-center text-text-secondary max-w-sm">
        <p className="text-base font-medium mb-2">
          Model configuration is managed by your administrator.
        </p>
        <p className="text-sm">
          Contact your workspace admin or log in to the admin panel to configure
          model providers and defaults.
        </p>
      </div>
    </div>
  );
};
export default ModelProviders;
