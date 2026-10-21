#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef int esp_err_t;
typedef int nvs_handle_t;
/* Types and functions below are extracted directly from the production source. */
#ifdef _MSC_VER
#define __attribute__(attributes)
#endif
#include "firmware_usb_types.inc"
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NVS_NOT_FOUND 1
#define ESP_ERR_INVALID_ARG 2
#define NVS_READWRITE 1
static int durable[4], staged[4], read_error, open_error, set_error, commit_error;
static int commits, erases, sets, fail_set_at, fail_commit_at, read_error_key=-1;
static int key_index(const char *key) {
    if (!strcmp(key,"output_mode")) return 0;
    if (!strcmp(key,"standalone")) return 1;
    if (!strcmp(key,"usb_hid")) return 2;
    if (!strcmp(key,"mode_migration")) return 3;
    assert(false); return -1;
}
static esp_err_t nvs_open(const char *ns, int flags, nvs_handle_t *nvs) {
    assert(!strcmp(ns,"s2p_profile")); (void)flags; *nvs=1;
    memcpy(staged,durable,sizeof(staged)); return open_error;
}
static esp_err_t nvs_get_u8(nvs_handle_t nvs, const char *key, uint8_t *value) {
    (void)nvs;
    if (read_error) return read_error;
    if (key_index(key)==read_error_key) return ESP_FAIL;
    int item=durable[key_index(key)];
    if (item<0) return ESP_ERR_NVS_NOT_FOUND;
    *value=(uint8_t)item; return ESP_OK;
}
static esp_err_t nvs_set_u8(nvs_handle_t nvs, const char *key, uint8_t value) {
    (void)nvs;
    sets++;
    if (set_error) return set_error;
    if (sets==fail_set_at) return ESP_FAIL;
    staged[key_index(key)]=value; return ESP_OK;
}
static esp_err_t nvs_erase_key(nvs_handle_t nvs,const char *key) {
    (void)nvs; erases++; staged[key_index(key)]=-1; return ESP_OK;
}
static esp_err_t nvs_commit(nvs_handle_t nvs) {
    (void)nvs; commits++;
    if(commit_error || commits==fail_commit_at) return ESP_FAIL;
    memcpy(durable,staged,sizeof(durable)); return ESP_OK;
}
static void nvs_close(nvs_handle_t nvs) {(void)nvs;}
static int s_state_mux;
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
#define pdMS_TO_TICKS(x) (x)
static int64_t clock_us;
static int restarts;
static int64_t esp_timer_get_time(void) { return clock_us; }
static void vTaskDelay(int ticks) {(void)ticks;}
static void esp_restart(void) {restarts++;}
static void (*s_wakeup_cb)(void);
static char s_usb_serial[17];
static esp_err_t esp_efuse_mac_get_default(uint8_t *mac) {
    const uint8_t fixed_mac[]={0x10,0x2a,0x3b,0x4c,0x5d,0xef};
    memcpy(mac,fixed_mac,6); return ESP_OK;
}
#define ESP_ERROR_CHECK(expr) assert((expr)==ESP_OK)
#include "firmware_usb_mode_functions.inc"
static void chord(uint32_t buttons, int64_t time) {
    clock_us=time;
    (void)filter_mode_chord(buttons);
    process_mode_switch_requests();
}
static void test_chords(void) {
    s_configured_output_mode=STANDALONE_OUTPUT_HID;
    chord(0,0);
    chord(0x1002,10);
    chord(0x1002,3000009);
    assert(restarts==0);
    chord(0x1002,3000010);
    assert(restarts==0);
    chord(0x1000,3000020);
    assert(restarts==0);
    chord(0,3000030);
    assert(restarts==1 && durable[0]==0);
    process_mode_switch_requests();
    assert(restarts==1);
    s_configured_output_mode=STANDALONE_OUTPUT_BRIDGE;
    chord(0x1008,4000000);
    chord(0x1008,7000000);
    assert(restarts==1);
    chord(0,7000010);
    assert(restarts==2 && durable[0]==2);
    s_configured_output_mode=STANDALONE_OUTPUT_HID;
    chord(0x1008,8000000); chord(0x1008,11000000); chord(0,11000010);
    assert(restarts==2);
    chord(0x1001,12000000); chord(0x1001,18000000); chord(0,18000010);
    assert(restarts==2);
    commit_error=ESP_FAIL;
    chord(0x1002,19000000); chord(0x1002,22000000); chord(0,22000010);
    assert(restarts==2 && durable[0]==2);
    process_mode_switch_requests();
    assert(restarts==2);
}

static standalone_output_mode_t s_output_mode=STANDALONE_OUTPUT_HID;
static standalone_output_mode_t s_active_output_mode=STANDALONE_OUTPUT_HID;
static char s_own_mac[]="11:22:33:44:55:66";
static unsigned s_event_queue_drops;
static char response[1024];
#define STANDALONE_PROFILE_SCHEMA 1
#define STANDALONE_PROFILE_MAX 8192
#ifdef _MSC_VER
#define __atomic_load_n(p, order) (*(p))
#define __ATOMIC_RELAXED 0
#endif
static unsigned ch_active_mask(void) {return 1;}
static bool bridge_host_mode(void) {return s_output_mode==0 && s_active_output_mode==0;}
static void send_json(const char *json) {assert(strlen(json)<sizeof(response)); strcpy(response,json);}
static const char *esp_err_to_name(esp_err_t err) {(void)err; return "FAIL";}
#include "firmware_usb_protocol_functions.inc"
static void test_protocol(void) {
    assert(!strcmp(standalone_usb_serial(),"S2P-102A3B4C5DEF"));
    send_status_response();
    assert(strstr(response,"\"version\":\"1.0.7\""));
    assert(strstr(response,"\"mode_migration\":"));
    assert(strstr(response,"\"usb_serial\":\"S2P-"));
    FILE *out=fopen("build/bridge-host-tests/usb_status.json","wb");
    assert(out); fputs(response,out); fclose(out);
    send_capabilities_response();
    assert(strstr(response,"\"mode_migration\":"));
    assert(strstr(response,"\"profile\":\"s2p_usb_bridge\""));
    assert(strstr(response,"\"build\":\"standalone_diagnostics\""));
    out=fopen("build/bridge-host-tests/usb_capabilities.json","wb");
    assert(out); fputs(response,out); fclose(out);
    int previous_commits=commits;
    do_mode_command("standalone");
    assert(strstr(response,"\"error\":\"unsupported\""));
    do_mode_command("standalone_auto");
    assert(strstr(response,"\"error\":\"unsupported\""));
    do_mode_command("garbage");
    assert(strstr(response,"\"error\":\"value\""));
    assert(commits==previous_commits);
    do_mode_command("standalone_hid");
    assert(strstr(response,"\"restart_required\":0"));
    do_mode_command("bridge");
    assert(strstr(response,"\"restart_required\":1"));
    assert(durable[0]==0);
    commit_error=ESP_FAIL;
    do_mode_command("standalone_hid");
    assert(strstr(response,"\"ok\":0"));
    assert(durable[0]==0);
}
static void reset(void) {
    for(int i=0;i<4;i++) durable[i]=-1;
    read_error=open_error=set_error=commit_error=commits=erases=sets=fail_set_at=fail_commit_at=0;
    read_error_key=-1;
}
static void test_report_encoding(void) {
    xinput_report_t input={0};
    uint8_t report[13];
    encode_hid_report(&input,report);
    const uint8_t neutral[13]={0,0,8,0,0,0,0,0,0,0,0,0,0};
    assert(!memcmp(report,neutral,13));
    input.buttons1=0xd9; /* up + right + start + both stick buttons */
    input.buttons2=0xd7; /* A + X + Y + shoulders + HOME */
    input.left_x=INT16_MIN;
    input.left_y=INT16_MIN;
    input.right_x=INT16_MAX;
    input.right_y=1;
    input.left_trigger=31;
    input.right_trigger=32;
    encode_hid_report(&input,report);
    const uint8_t expected[13]={0xbd,0x47,1,0,0x80,0xff,0x7f,0xff,0x7f,0xff,0xff,31,32};
    assert(!memcmp(report,expected,13));
    assert(hid_hat(0x0f)==8); /* opposing directions are neutral */
    assert(hid_hat(0x06)==5); /* down-left */
}
int main(void) {
    const int stored[] = {0,1,2,3,4,255,-1};
    const int expected[] = {0,2,2,2,0,0,0};
    for (int i=0;i<7;i++) {
        reset(); durable[0]=stored[i];
        assert((int)standalone_output_mode_load()==expected[i]);
        if (stored[i]==1 || stored[i]==3) {
            assert(durable[0]==2);
            assert(durable[3]==stored[i]);
            assert(!strcmp(standalone_output_mode_migration(),
                stored[i]==1 ? "standalone" : "standalone_auto"));
            int previous_commits=commits;
            assert(standalone_output_mode_load()==2);
            assert(commits==previous_commits);
            assert(!strcmp(standalone_output_mode_migration(),
                stored[i]==1 ? "standalone" : "standalone_auto"));
        } else assert(!strcmp(standalone_output_mode_migration(),""));
    }
    for(int standalone=0;standalone<2;standalone++) {
        for(int hid=0;hid<2;hid++) {
            reset(); durable[1]=standalone; durable[2]=hid;
            assert((int)standalone_output_mode_load()==(standalone ? 2 : 0));
            assert(durable[0]==(standalone ? 2 : 0));
            assert(durable[1]==-1 && durable[2]==-1);
            assert(!strcmp(standalone_output_mode_migration(),
                standalone && !hid ? "standalone" : ""));
            assert((int)standalone_output_mode_load()==(standalone ? 2 : 0));
        }
    }
    reset(); durable[1]=2;
    assert(standalone_output_mode_load()==0 && commits==0);
    reset(); durable[2]=1;
    assert(standalone_output_mode_load()==0);
    for(int mode=1;mode<=3;mode+=2) {
        reset(); durable[0]=mode; commit_error=ESP_FAIL;
        assert(standalone_output_mode_load()==2);
        assert(durable[0]==mode && erases==0);
        assert(standalone_output_mode_load()==2);
        commit_error=0;
        assert(standalone_output_mode_load()==2 && durable[0]==2);
        reset(); durable[0]=mode; set_error=ESP_FAIL;
        assert(standalone_output_mode_load()==2 && durable[0]==mode);
        assert(erases==0);
    }
    reset(); durable[1]=1; durable[2]=0; commit_error=ESP_FAIL;
    assert(standalone_output_mode_load()==2);
    assert(durable[1]==1 && durable[2]==0 && erases==0);
    commit_error=0;
    assert(standalone_output_mode_load()==2 && durable[0]==2);
    assert(durable[1]==-1 && durable[2]==-1);
    reset(); durable[0]=1; open_error=ESP_FAIL;
    assert(standalone_output_mode_load()==0 && commits==0);
    reset(); durable[0]=1; read_error=ESP_FAIL;
    assert(standalone_output_mode_load()==0 && commits==0);
    reset();
    assert(standalone_output_mode_store((standalone_output_mode_t)1)==ESP_ERR_INVALID_ARG);
    assert(standalone_output_mode_store((standalone_output_mode_t)3)==ESP_ERR_INVALID_ARG);
    assert(standalone_output_mode_store((standalone_output_mode_t)255)==ESP_ERR_INVALID_ARG);
    assert(commits==0);
    assert(standalone_output_mode_store(STANDALONE_OUTPUT_HID)==ESP_OK && durable[0]==2);
    commit_error=ESP_FAIL;
    assert(standalone_output_mode_store(STANDALONE_OUTPUT_BRIDGE)==ESP_FAIL && durable[0]==2);
    for(int key=0;key<=2;key++) {
        reset(); read_error_key=key; durable[1]=1;
        assert(standalone_output_mode_load()==0 && commits==0 && erases==0);
    }
    for(int step=1;step<=2;step++) {
        reset(); durable[1]=1; fail_set_at=step;
        assert(standalone_output_mode_load()==2);
        assert(durable[1]==1 && erases==0);
    }
    reset(); durable[1]=1; fail_commit_at=2; /* cleanup commit fails */
    assert(standalone_output_mode_load()==2);
    assert(durable[0]==2 && durable[1]==1 && durable[3]==1);
    fail_commit_at=0;
    assert(standalone_output_mode_load()==2);
    assert(!strcmp(standalone_output_mode_migration(),"standalone"));
    reset(); durable[0]=2; durable[3]=3; /* source survives a partial prior boot */
    assert(standalone_output_mode_load()==2);
    assert(!strcmp(standalone_output_mode_migration(),"standalone_auto"));
    assert(restarts==0); /* no load/migration path can reboot */
    reset(); durable[0]=2;
    test_report_encoding();
    test_chords();
    commit_error=0;
    s_mode_migration=3;
    test_protocol();
    puts("all scenarios passed");
    return 0;
}
