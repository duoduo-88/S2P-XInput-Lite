/* Production ingress, CDC maintenance, and HID pump with USB deliberately stalled. */
#define main firmware_mode_scenarios_main
#include "firmware_usb_modes_test.c"
#undef main
#define MAX_CH 8
#define REPORT_SIZE 64
#define INPUT_EDGE_QUEUE_SIZE 32
typedef int esp_gatt_if_t;
static struct { bool used, ready, prefer_legacy; uint32_t generation; } s_ch[MAX_CH];
typedef struct {
    uint8_t ch, len;
    uint16_t handle;
    uint32_t generation;
    uint8_t data[REPORT_SIZE];
} in_report_t;
static in_report_t s_in_shadow[MAX_CH], s_input_edge_queue[INPUT_EDGE_QUEUE_SIZE];
static bool s_in_dirty[MAX_CH], s_last_input_digital_valid[MAX_CH];
static uint32_t s_last_input_digital[MAX_CH];
static uint8_t s_input_edge_head, s_input_edge_tail, s_input_edge_count;
static int s_in_mux, s_cdc_task_h;
static struct { unsigned notify_queue_drops, shadow_overwrites; } s_input_latency_metrics;
static bool s_standalone_mode=true, s_usb_hid_mode=true, s_hid_report_dirty=true;
static int s_active_channel=-1;
static bool s_idle_baseline_valid;
static uint32_t s_last_activity_ms;
static xinput_report_t s_pending_report;
static bool usb_configured, usb_endpoint_ready;
static uint32_t read_u32_le(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static uint32_t idle_now_ms(void) {return (uint32_t)(clock_us/1000);}
static void xTaskNotifyGive(int task) {(void)task;}
static bool tud_ready(void) {return usb_configured;}
static bool tud_hid_ready(void) {return usb_endpoint_ready;}
static bool tud_hid_report(uint8_t id, const void *data, uint16_t length) {
    (void)id; (void)data; (void)length; return usb_endpoint_ready;
}
static void cancel_usb_wait(void) {}
static void note_usb_wait_if_pending(void) {}
static void finish_usb_wait(void) {}
static void pump_gatt_outputs(void) {}
#include "firmware_usb_recovery_functions.inc"
static void incoming(int channel, uint32_t buttons, int64_t now) {
    uint8_t raw[16]={0};
    raw[4]=(uint8_t)buttons; raw[5]=(uint8_t)(buttons>>8);
    raw[6]=(uint8_t)(buttons>>16); raw[7]=(uint8_t)(buttons>>24);
    clock_us=now;
    queue_input_state(channel,0,raw,sizeof(raw));
    maintenance();
}
static void bridge_incoming(int channel, uint32_t buttons, int64_t now) {
    incoming(channel,buttons,now);
    bridge_forward(channel);
    maintenance();
}
int main(void) {
    reset(); restarts=0;
    s_configured_output_mode=STANDALONE_OUTPUT_HID;
    s_ch[0].used=s_ch[0].ready=true; s_ch[0].generation=1;
    incoming(0,0,0);
    assert(!standalone_xinput_can_accept_input());
    incoming(0,4,1); /* An ordinary button press/release must remain queued. */
    incoming(0,0,2);
    incoming(0,0x1002,10);
    incoming(0,0x1002,3000010);
    assert(restarts==0);
    incoming(0,0,3000020);
    assert(restarts==1 && durable[0]==0);
    assert(s_hid_report_dirty && s_input_edge_count==5);
    in_report_t edge;
    assert(take_input_edge(&edge) && read_u32_le(edge.data+4)==0);
    assert(take_input_edge(&edge) && read_u32_le(edge.data+4)==4);
    assert(take_input_edge(&edge) && read_u32_le(edge.data+4)==0);
    assert(take_input_edge(&edge) && read_u32_le(edge.data+4)==0x1002);
    assert(take_input_edge(&edge) && read_u32_le(edge.data+4)==0);
    assert(!take_input_edge(&edge));
    assert(!take_latest_input_if_no_edges(0,&edge));

    /* Another controller cannot complete the active controller's chord. */
    s_ch[1].used=s_ch[1].ready=true; s_ch[1].generation=2;
    incoming(0,0x1002,4000000);
    incoming(1,0x1002,7000000);
    incoming(1,0,7000010);
    assert(restarts==1);
    /* Disconnect cancels the armed request before a late release arrives. */
    standalone_xinput_forget_channel(0);
    s_ch[0].used=false; s_ch[0].ready=false; s_ch[0].generation=3;
    incoming(1,0,7000020);
    assert(restarts==1);
    s_ch[0].used=s_ch[0].ready=true; /* Old snapshot has generation 1. */
    clock_us=11000000; maintenance();
    assert(restarts==1);
    /* Endpoint backpressure (configured but busy) must also permit rescue. */
    usb_configured=true;
    incoming(1,0x1002,12000000);
    incoming(1,0x1002,15000000);
    incoming(1,0,15000010);
    assert(restarts==2);

    /* Bridge's chord owner is independent of HID's output channel selection. */
    while(take_input_edge(&edge)) {}
    standalone_xinput_forget_channel(1);
    s_standalone_mode=false; s_usb_hid_mode=false;
    s_configured_output_mode=STANDALONE_OUTPUT_BRIDGE;
    bridge_incoming(0,0x1008,16000000);
    bridge_incoming(1,0,17000000);
    bridge_incoming(0,0x1008,19000000);
    bridge_incoming(1,0,19000010);
    assert(restarts==2); /* Other controllers cannot release the owner's chord. */
    standalone_xinput_forget_channel(0);
    bridge_incoming(1,0,19000020);
    assert(restarts==2); /* Disconnect cancels the armed owner. */
    bridge_incoming(1,0x1008,20000000);
    bridge_incoming(1,0x1008,23000000);
    bridge_incoming(1,0,23000010);
    assert(restarts==3 && durable[0]==2);
    /* A disconnected candidate cannot donate its elapsed hold time either. */
    bridge_incoming(0,0x1008,24000000);
    standalone_xinput_forget_channel(0);
    bridge_incoming(1,0x1008,27000000);
    bridge_incoming(1,0,27000010);
    assert(restarts==3);
    bridge_incoming(0,0x1008,28000000);
    bridge_incoming(0,0x1008,31000000);
    incoming(0,0,31000010);
    bridge_forward(0); /* Release observed, controlled restart not processed yet. */
    assert(s_mode_switch_requested);
    standalone_xinput_forget_channel(0);
    maintenance();
    assert(restarts==3 && !s_mode_switch_requested);
    puts("all scenarios passed");
    return 0;
}
