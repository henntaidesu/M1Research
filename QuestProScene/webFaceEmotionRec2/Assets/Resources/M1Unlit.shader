// M1/Unlit：不受光照的纯色 × 贴图，写深度、双面。
// 给运行时用代码搭的刺激物体用（模仿片段的视频面片、StartleDoorStimulus 的房间几何）。
// 放在 Resources 里保证一定被打进 APK（Resources.Load<Shader>("M1Unlit")）；
// Sprites/Default 虽然 Always Included，但不写深度，多个物体叠在一起时前后顺序会乱。
// 带立体实例化宏，Quest 的 Multiview 下左右眼都能画出来。
Shader "M1/Unlit"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Color ("Color", Color) = (1,1,1,1)
    }
    SubShader
    {
        Tags { "Queue"="Geometry" "RenderType"="Opaque" }
        Cull Off
        ZWrite On

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float4 _MainTex_ST;
            fixed4 _Color;

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };
            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            v2f vert(appdata v)
            {
                v2f o;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_OUTPUT(v2f, o);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                return o;
            }

            fixed4 frag(v2f i) : SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
                return tex2D(_MainTex, i.uv) * _Color;
            }
            ENDCG
        }
    }
    Fallback Off
}
