#version 300 es
precision highp float;

in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;

const vec2 SCREEN = vec2(2560.0, 1600.0);
const float RADIUS = 28.0;

void main() {
    vec4 c = texture(tex, v_texcoord);

    vec2 pix = v_texcoord * SCREEN;
    vec2 corner = min(pix, SCREEN - pix);

    if (corner.x < RADIUS && corner.y < RADIUS) {
        vec2 d = vec2(RADIUS) - corner;
        float dist = length(d);
        float aa = smoothstep(RADIUS - 1.0, RADIUS + 0.5, dist);
        c.rgb = mix(c.rgb, vec3(0.0), aa);
        c.a = mix(c.a, 1.0, aa);
    }

    fragColor = c;
}
