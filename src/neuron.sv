
module weight_compute(
input signed [7:0][3:0] prev_activation,
   input signed [7:0][3:0] weight,
   input signed [3:0] bais,
   output signed [3:0] act_out
);

   logic signed [7:0] mul_result [0:7];

   always_comb begin
      integer j;
      for(j = 0; j < 8; j = j + 1) begin
         mul_result[j] = $signed(prev_activation[j]) * $signed(weight[j]);
      end
   end

   logic signed [3:0] out_sum;
   always_comb begin
      out_sum = '0; // Reset sum to 0 before accumulation
      for (int i = 0; i < 8; i++) begin
         out_sum = out_sum + mul_result[i][5:2];
      end
   end

   assign act_out = out_sum + bais;
   
endmodule