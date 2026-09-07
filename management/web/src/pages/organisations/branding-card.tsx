// CUSTOM B2B SaaS — DA (identité visuelle) par organisation.
// L'org_admin téléverse un logo et choisit une couleur d'accent ; le front
// produit les applique après login (hook use-org-branding). Effacer les
// deux champs ramène le thème Cyllene par défaut.
import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  ColorPicker,
  Radio,
  Space,
  Typography,
  Upload,
} from "antd";
import { DeleteOutlined, UploadOutlined } from "@ant-design/icons";
import api from "@/lib/api";

const { Text } = Typography;

const LOGO_MAX_BYTES = 300_000;
const BANNER_MAX_BYTES = 1_000_000;
const ACCEPTED = "image/png,image/jpeg,image/svg+xml,image/webp";

interface Branding {
  logo: string | null;
  brand_color: string | null;
  banner_mode: "cyllene" | "org";
  banner: string | null;
}

export default function BrandingCard({ orgId }: { orgId: string }) {
  const { message } = App.useApp();
  const [branding, setBranding] = useState<Branding>({
    logo: null,
    brand_color: null,
    banner_mode: "cyllene",
    banner: null,
  });
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/orgs/${orgId}/branding`);
      setBranding({
        logo: data?.logo ?? null,
        brand_color: data?.brand_color ?? null,
        banner_mode: data?.banner_mode === "org" ? "org" : "cyllene",
        banner: data?.banner ?? null,
      });
      setDirty(false);
    } catch {
      // silencieux : la carte reste sur les valeurs par défaut
    }
  }, [orgId]);

  useEffect(() => {
    load();
  }, [load]);

  const onLogoFile = (file: File) => {
    if (file.size > LOGO_MAX_BYTES) {
      message.error(`Logo trop lourd (max ${LOGO_MAX_BYTES / 1000} Ko)`);
      return Upload.LIST_IGNORE;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setBranding((b) => ({ ...b, logo: String(reader.result) }));
      setDirty(true);
    };
    reader.readAsDataURL(file);
    return false; // pas d'upload auto : on envoie au clic Enregistrer
  };

  const onBannerFile = (file: File) => {
    if (file.size > BANNER_MAX_BYTES) {
      message.error(`Bannière trop lourde (max ${BANNER_MAX_BYTES / 1000} Ko)`);
      return Upload.LIST_IGNORE;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setBranding((b) => ({ ...b, banner: String(reader.result) }));
      setDirty(true);
    };
    reader.readAsDataURL(file);
    return false;
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.put(`/orgs/${orgId}/branding`, {
        logo: branding.logo ?? "",
        brand_color: branding.brand_color ?? "",
        banner_mode: branding.banner_mode,
        banner: branding.banner ?? "",
      });
      message.success("Identité visuelle enregistrée");
      setDirty(false);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || "Échec de l'enregistrement");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="Identité visuelle"
      extra={
        <Button
          type="primary"
          onClick={save}
          loading={saving}
          disabled={!dirty}
        >
          Enregistrer
        </Button>
      }
    >
      <div className="space-y-6">
        <div>
          <Text strong>Logo</Text>
          <div className="text-gray-500 text-sm mb-2">
            Affiché dans l'en-tête du produit pour les membres de cette
            organisation. PNG, JPEG, SVG ou WebP, 300 Ko max.
          </div>
          <Space align="center" size="large">
            <div className="border rounded p-2 bg-gray-50 min-w-[120px] text-center">
              {branding.logo ? (
                <img
                  src={branding.logo}
                  alt="logo"
                  style={{ maxHeight: 40, maxWidth: 200 }}
                />
              ) : (
                <Text type="secondary">Logo Cyllene (défaut)</Text>
              )}
            </div>
            <Upload
              accept={ACCEPTED}
              showUploadList={false}
              beforeUpload={onLogoFile}
            >
              <Button icon={<UploadOutlined />}>Choisir un fichier</Button>
            </Upload>
            {branding.logo && (
              <Button
                icon={<DeleteOutlined />}
                onClick={() => {
                  setBranding((b) => ({ ...b, logo: null }));
                  setDirty(true);
                }}
              >
                Retirer
              </Button>
            )}
          </Space>
        </div>

        <div>
          <Text strong>Couleur d'accent</Text>
          <div className="text-gray-500 text-sm mb-2">
            Boutons, liens et éléments actifs du produit. Vide = bleu Cyllene.
          </div>
          <Space align="center" size="large">
            <ColorPicker
              value={branding.brand_color || "#2d6dbb"}
              format="hex"
              disabledAlpha
              onChangeComplete={(c) => {
                setBranding((b) => ({ ...b, brand_color: c.toHexString() }));
                setDirty(true);
              }}
            />
            <Text code>{branding.brand_color || "défaut"}</Text>
            {branding.brand_color && (
              <Button
                icon={<DeleteOutlined />}
                onClick={() => {
                  setBranding((b) => ({ ...b, brand_color: null }));
                  setDirty(true);
                }}
              >
                Réinitialiser
              </Button>
            )}
          </Space>
        </div>

        <div>
          <Text strong>Bannière d'accueil</Text>
          <div className="text-gray-500 text-sm mb-2">
            Le visuel en haut de la page d'accueil des membres. Par défaut la
            montagne Cyllene ; sinon celle de l'organisation : votre image, ou à
            défaut un aplat de votre couleur d'accent avec votre logo.
          </div>
          <Radio.Group
            value={branding.banner_mode}
            onChange={(e) => {
              setBranding((b) => ({ ...b, banner_mode: e.target.value }));
              setDirty(true);
            }}
            options={[
              { label: "Cyllene (défaut)", value: "cyllene" },
              { label: "Celle de l'organisation", value: "org" },
            ]}
          />
          {branding.banner_mode === "org" && (
            <div className="mt-3">
              <Space align="center" size="large">
                <div
                  className="border rounded overflow-hidden text-center"
                  style={{ width: 240, height: 64, background: "#f5f5f5" }}
                >
                  {branding.banner ? (
                    <img
                      src={branding.banner}
                      alt="bannière"
                      style={{
                        width: "100%",
                        height: "100%",
                        objectFit: "cover",
                      }}
                    />
                  ) : (
                    <div
                      style={{
                        width: "100%",
                        height: "100%",
                        background: branding.brand_color || "#2d6dbb",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "flex-end",
                        paddingRight: 12,
                      }}
                    >
                      {branding.logo && (
                        <img
                          src={branding.logo}
                          alt=""
                          style={{ maxHeight: 28, maxWidth: 90, opacity: 0.9 }}
                        />
                      )}
                    </div>
                  )}
                </div>
                <Upload
                  accept={ACCEPTED}
                  showUploadList={false}
                  beforeUpload={onBannerFile}
                >
                  <Button icon={<UploadOutlined />}>Choisir une image</Button>
                </Upload>
                {branding.banner && (
                  <Button
                    icon={<DeleteOutlined />}
                    onClick={() => {
                      setBranding((b) => ({ ...b, banner: null }));
                      setDirty(true);
                    }}
                  >
                    Retirer l'image
                  </Button>
                )}
              </Space>
              <div className="text-gray-500 text-sm mt-2">
                Image large conseillée, 1600 × 320 px environ, 1 Mo max. Sans
                image, la bannière est générée depuis la couleur et le logo.
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
