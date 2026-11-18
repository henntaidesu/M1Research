// 自定义等距柱状（equirectangular / latitude-longitude, 360°）全景天空盒。
// 用工程内自带 shader + Resources 材质，避免内置 Skybox/Panoramic 打包被裁。
// 由 VREmotionSelectionUI 通过 Resources.Load<Material>("PanoramaSkybox") 加载并设 _MainTex。
Shader "Skybox/PanoramaCustom"
{
    Properties
    {
        [NoScaleOffset] _MainTex ("Panorama (Equirectangular)", 2D) = "grey" {}
        _Exposure ("Exposure", Range(0, 8)) = 1.0
        _Rotation ("Rotation", Range(0, 360)) = 0
    }
    SubShader
    {
        Tags { "Queue"="Background" "RenderType"="Background" "PreviewType"="Skybox" }
        Cull Off
        ZWrite Off

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            half _Exposure;
            float _Rotation;

            struct appdata { float4 vertex : POSITION; };
            struct v2f { float4 pos : SV_POSITION; float3 dir : TEXCOORD0; };

            float3 RotateAroundY(float3 v, float deg)
            {
                float rad = deg * (UNITY_PI / 180.0);
                float s = sin(rad);
                float c = cos(rad);
                return float3(c * v.x + s * v.z, v.y, -s * v.x + c * v.z);
            }

            v2f vert(appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.dir = v.vertex.xyz;
                return o;
            }

            #define INV_TWO_PI 0.15915494 // 1/(2*PI)
            #define INV_PI     0.31830989 // 1/PI

            fixed4 frag(v2f i) : SV_Target
            {
                float3 dir = normalize(RotateAroundY(i.dir, _Rotation));
                float2 uv;
                // 经度 0..1：+Z（用户初始正对方向）落在图片正中 u=0.5，
                // 左右接缝甩到正后方 -Z；用 -dir.z 会把接缝正对着用户。
                uv.x = atan2(dir.x, dir.z) * INV_TWO_PI + 0.5;
                uv.y = 1.0 - acos(clamp(dir.y, -1.0, 1.0)) * INV_PI;  // 纬度：上=1 下=0
                // 经度缠绕缝处 uv.x 的屏幕空间导数会突跳 ~1.0，令 tex2D 选到最粗 mip → 明显竖缝。
                // 修正 x 方向导数（去掉缠绕整数跳变）后用 tex2Dgrad 采样，消除接缝且保留清晰度。
                float2 dx = ddx(uv);
                float2 dy = ddy(uv);
                dx.x -= round(dx.x);
                dy.x -= round(dy.x);
                fixed4 col = tex2Dgrad(_MainTex, uv, dx, dy);
                col.rgb *= _Exposure;
                return col;
            }
            ENDCG
        }
    }
    Fallback Off
}
